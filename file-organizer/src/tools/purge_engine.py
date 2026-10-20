"""Purge engine: the only place (besides release_exec/housekeep_exec) with a
delete primitive, deliberately OUTSIDE gov/ so PRD I1 stays intact.

Fail-closed by construction:

  - POSITIVE WHITELIST ONLY. A path is purgeable only if it matches one of the
    explicit rules below AND passes the frozen-prefix gate AND (for file rules)
    the per-file re-check at delete time. There is no "everything else" path.
  - FROZEN PREFIXES are checked first: Windows, WinREAgent, Recycle Bins,
    Program Files, pagefile/hiberfil, Windows Installer, the IM-client tree,
    D:\\Data, D:\\DataArea (state handled by housekeep), and the user's
    document folders are unreachable from any rule.
  - BUDGET: --budget-mib / --max-items / --max-seconds are hard caps; the run
    stops mid-way and reports what it held back.
  - HOLD: a file at .state/purge/HOLD refuses every run.
  - JOURNAL: every run writes a header, one line per file (planned/done/
    failed with bytes), and a footer with per-volume free bytes before/after.
  - REPARSE: symlinks/junctions are never descended or deleted.

Rules (first round, all verified-regenerable or verified-duplicate):
  desktop-staging-verified  C: desktop-triage trash, ONLY entries the P0-2
                            audit proved byte-identical to their D: copy;
                            the D: digest is re-verified at purge time.
  user-temp-old             %LOCALAPPDATA%\\Temp files older than 7 days.
  claude-3p-partials        *.partial / *.crdownload download fragments.
  wer-old                   WER ReportQueue/Temp error reports > 30 days.
  manual-user-ordered       the ff_v3_tmp folder the user ordered deleted
                            (2026-10-19); pre-purge digest is journaled.
  npm-cache                 official `npm cache clean --force` (preferred over
                            hand deletion); measured before/after.

Usage:
    python tools/purge_engine.py plan [--rules a,b] [--budget-mib N]
    python tools/purge_engine.py run --approve [--rules a,b] [--budget-mib N]
    python tools/purge_engine.py hold | resume | status
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, ".state", "purge")
JOURNAL = os.path.join(STATE, "journal.ndjson")
HOLD = os.path.join(STATE, "HOLD")
AUDIT = os.path.join(REPO, ".state", "desktop_triage",
                     "audit_20261019_173154.json")

VOLUMES = ("C:", "D:")
REPARSE_ATTR = 0x400
FROZEN_PREFIXES = (
    "c:\\windows", "c:\\$winreagent", "c:\\$recycle.bin", "c:\\$windows.~bt",
    "c:\\program files", "c:\\program files (x86)", "c:\\pagefile.sys",
    "c:\\hiberfil.sys", "c:\\swapfile.sys", "c:\\windows\\installer",
    "c:\\users\\example\\appdata\\roaming\\microsoft\\windows",
    "c:\\users\\example\\desktop", "c:\\users\\example\\documents",
    "c:\\users\\example\\pictures", "c:\\users\\example\\videos",
    "c:\\users\\example\\music", "c:\\users\\example\\downloads",
    "d:\\dataarea\\chatclient", "d:\\data", "d:\\dataarea",
    "d:\\_trash",
)
NPM = r"C:\Users\example\.workbuddy\binaries\node\versions\22.22.2-6\npm.cmd"
TEMP = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp")
MANUAL = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                      "desktop-triage-trash", "manual_20261019_150618")
WER_DIRS = ("ReportQueue", "Temp")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def append_j(obj: dict) -> None:
    os.makedirs(STATE, exist_ok=True)
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def disk_free() -> dict:
    out = {}
    for v in VOLUMES:
        out[v] = shutil.disk_usage(v + os.sep).free
    return out


def low(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def frozen(path: str) -> bool:
    p = low(path)
    return any(p == f or p.startswith(f + os.sep) for f in FROZEN_PREFIXES)


def is_reparse(path: str) -> bool:
    try:
        if os.path.islink(path):
            return True
        st = os.stat(path, follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & REPARSE_ATTR)
    except OSError:
        return True


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root: str):
    """(digest, files, bytes); raises on reparse inside the tree."""
    rows = []
    for base, dirs, files in os.walk(root, followlinks=False):
        for d in list(dirs):
            if is_reparse(os.path.join(base, d)):
                raise RuntimeError("reparse in tree: %s" % os.path.join(base, d))
        for fn in files:
            fp = os.path.join(base, fn)
            if is_reparse(fp):
                raise RuntimeError("reparse file: %s" % fp)
            st = os.stat(fp, follow_symlinks=False)
            rows.append((os.path.relpath(fp, root).replace("\\", "/"),
                         st.st_size, sha256_file(fp)))
    rows.sort()
    h = hashlib.sha256()
    for rel, size, sha in rows:
        h.update(("%s\0%d\0%s\n" % (rel, size, sha)).encode("utf-8"))
    return h.hexdigest(), len(rows), sum(r[1] for r in rows)


def dir_size(root: str) -> tuple:
    n = b = 0
    for base, dirs, files in os.walk(root):
        for d in list(dirs):
            if is_reparse(os.path.join(base, d)):
                dirs.remove(d)
        for fn in files:
            try:
                b += os.path.getsize(os.path.join(base, fn))
                n += 1
            except OSError:
                pass
    return n, b


def prune_empty(root: str) -> None:
    for base, dirs, files in os.walk(root, topdown=False):
        if base == root:
            continue
        try:
            if not os.listdir(base):
                os.rmdir(base)
        except OSError:
            pass


# ---------------------------------------------------------------- rules ----
def rule_desktop_staging():
    """Yield (rule, path, kind, bytes, verify_callable) for verified staging."""
    if not os.path.isfile(AUDIT):
        return
    with open(AUDIT, encoding="utf-8") as f:
        audit = json.load(f)
    for it in audit.get("items", []):
        if it.get("status") != "VERIFIED_PURGEABLE":
            continue
        trash = it.get("trash", "")
        if not trash or frozen(trash):
            continue
        yield ("desktop-staging-verified", trash, "tree",
               it.get("staging_bytes", 0), it)


def rule_user_temp_old(min_days: float = 7.0):
    root = TEMP
    if not os.path.isdir(root):
        return
    cutoff = time.time() - min_days * 86400
    for base, dirs, files in os.walk(root):
        for d in list(dirs):
            if is_reparse(os.path.join(base, d)):
                dirs.remove(d)
        for fn in files:
            fp = os.path.join(base, fn)
            try:
                st = os.stat(fp, follow_symlinks=False)
            except OSError:
                continue
            if st.st_mtime >= cutoff or frozen(fp):
                continue
            yield ("user-temp-old", fp, "file", st.st_size, None)


def rule_partials():
    for root, pats in ((os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                     "Claude-3p"), (".partial", ".crdownload")),):
        if not os.path.isdir(root):
            continue
        for base, dirs, files in os.walk(root):
            for d in list(dirs):
                if is_reparse(os.path.join(base, d)):
                    dirs.remove(d)
            for fn in files:
                if not fn.lower().endswith(pats):
                    continue
                fp = os.path.join(base, fn)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                if not frozen(fp):
                    yield ("claude-3p-partials", fp, "file", sz, None)


def rule_wer_old(min_days: float = 30.0):
    root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft",
                        "Windows", "WER")
    if not os.path.isdir(root):
        return
    cutoff = time.time() - min_days * 86400
    for sub in WER_DIRS:
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for base, dirs, files in os.walk(d):
            for fn in files:
                fp = os.path.join(base, fn)
                try:
                    st = os.stat(fp, follow_symlinks=False)
                except OSError:
                    continue
                if st.st_mtime >= cutoff or frozen(fp):
                    continue
                yield ("wer-old", fp, "file", st.st_size, None)


def rule_manual_user_ordered():
    if not os.path.isdir(MANUAL):
        return
    n, b = dir_size(MANUAL)
    if n:
        yield ("manual-user-ordered", MANUAL, "tree", b, None)


FILE_RULES = {
    "user-temp-old": rule_user_temp_old,
    "claude-3p-partials": rule_partials,
    "wer-old": rule_wer_old,
}
TREE_RULES = {
    "desktop-staging-verified": rule_desktop_staging,
    "manual-user-ordered": rule_manual_user_ordered,
}
OFFICIAL_RULES = {"npm-cache"}


def collect(rules: list[str]):
    """-> list of (rule, path, kind, bytes, extra) in rule order."""
    out = []
    for r in rules:
        if r in FILE_RULES:
            out.extend(FILE_RULES[r]())
        elif r in TREE_RULES:
            out.extend(TREE_RULES[r]())
        elif r == "npm-cache":
            root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm-cache")
            n, b = dir_size(root) if os.path.isdir(root) else (0, 0)
            out.append(("npm-cache", root, "official", b, None))
    return out


def verify_tree_again(path: str, extra: dict) -> str:
    """Re-verify a staging tree against the audit BEFORE deleting."""
    if extra is None:
        return ""
    try:
        dig, _, _ = tree_digest(path)
    except (OSError, RuntimeError) as exc:
        return "re-verify failed: %s" % exc
    if extra.get("trash_digest") and dig != extra["trash_digest"]:
        return "C-side digest changed since audit"
    dst = extra.get("dst", "")
    if dst and os.path.lexists(dst):
        try:
            ddig, _, _ = tree_digest(dst)
        except (OSError, RuntimeError) as exc:
            return "D-side unreadable: %s" % exc
        if ddig != extra.get("dst_digest"):
            return "D-side digest changed since audit"
    elif dst:
        return "D-side destination missing"
    return ""


def purge_file(path: str) -> int:
    if frozen(path) or is_reparse(path) or not os.path.isfile(path):
        return -1
    try:
        sz = os.path.getsize(path)
        os.remove(path)
        return sz
    except OSError:
        return -1


def purge_tree(root: str) -> tuple:
    freed = n = 0
    for base, dirs, files in os.walk(root, topdown=False):
        for d in list(dirs):
            if is_reparse(os.path.join(base, d)):
                dirs.remove(d)
        for fn in files:
            fp = os.path.join(base, fn)
            if frozen(fp) or is_reparse(fp):
                continue
            try:
                sz = os.path.getsize(fp)
                os.remove(fp)
                freed += sz
                n += 1
            except OSError:
                pass
    prune_empty(root)
    return freed, n


def run_official_npm(root: str) -> tuple:
    n0, b0 = dir_size(root) if os.path.isdir(root) else (0, 0)
    try:
        r = subprocess.run(["cmd", "/c", NPM, "cache", "clean", "--force"],
                           capture_output=True, text=True, timeout=300,
                           errors="replace")
        ok = r.returncode == 0
        out = (r.stdout or "")[-200:]
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, 0, str(exc)
    n1, b1 = dir_size(root) if os.path.isdir(root) else (0, 0)
    return (b0 - b1) if ok else -1, max(0, n0 - n1), out


# ------------------------------------------------------------------ main ----
def main() -> int:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"
    approve = "--approve" in argv
    rules = ["desktop-staging-verified", "user-temp-old", "claude-3p-partials",
             "wer-old", "manual-user-ordered", "npm-cache"]
    budget_mib, max_items, max_s = 950, 20000, 600
    for i, a in enumerate(argv):
        if a == "--rules" and i + 1 < len(argv):
            rules = [x for x in argv[i + 1].split(",") if x]
        if a == "--budget-mib" and i + 1 < len(argv):
            budget_mib = int(argv[i + 1])
        if a == "--max-items" and i + 1 < len(argv):
            max_items = int(argv[i + 1])
        if a == "--max-seconds" and i + 1 < len(argv):
            max_s = int(argv[i + 1])

    if cmd == "hold":
        os.makedirs(STATE, exist_ok=True)
        with open(HOLD, "w", encoding="utf-8") as f:
            f.write(now_iso() + "\n")
        print("HOLD set: %s" % HOLD)
        return 0
    if cmd == "resume":
        if os.path.exists(HOLD):
            os.remove(HOLD)
        print("HOLD cleared")
        return 0
    if cmd == "status":
        print("HOLD present: %s" % os.path.exists(HOLD))
        print("journal: %s (%d lines)" % (JOURNAL,
              sum(1 for _ in open(JOURNAL, encoding="utf-8"))
              if os.path.exists(JOURNAL) else 0))
        return 0

    if os.path.exists(HOLD):
        print("REFUSED: HOLD present: %s" % HOLD)
        return 2

    items = collect(rules)
    total_bytes = sum(i[3] for i in items if i[3] > 0)
    print("rules=%s candidates=%d est_bytes=%.1f MiB budget=%d MiB"
          % (",".join(rules), len(items), total_bytes / 1048576.0, budget_mib))
    for r, p, k, b, _ in items[:12]:
        print("  %-26s %10.1f MiB  %s" % (r, max(b, 0) / 1048576.0, p[-80:]))
    if len(items) > 12:
        print("  ... %d more" % (len(items) - 12))

    if cmd != "run":
        print("dry-run only; pass `run --approve` to execute")
        return 0
    if not approve:
        print("REFUSED: run requires --approve")
        return 2

    free_before = disk_free()
    run_id = time.strftime("%Y%m%d_%H%M%S")
    append_j({"kind": "run", "run_id": run_id, "ts": now_iso(),
              "rules": rules, "budget_mib": budget_mib,
              "candidates": len(items), "est_bytes": total_bytes,
              "free_before": free_before, "approve": True})

    budget = budget_mib * 1048576
    deadline = time.time() + max_s
    freed = files_done = failed = held = 0
    for r, p, k, b, extra in items:
        remaining = budget - freed
        if b > 0 and b > remaining:
            # a single item larger than the remaining budget is held back,
            # never partially executed (trees are atomic per run)
            append_j({"kind": "event", "run_id": run_id, "rule": r, "path": p,
                      "state": "held", "reason": "budget: item %.1f MiB > remaining %.1f MiB"
                      % (b / 1048576.0, remaining / 1048576.0), "ts": now_iso()})
            held += 1
            continue
        if freed >= budget or files_done >= max_items or time.time() > deadline:
            held += 1
            continue
        if k == "official":
            got, n, info = run_official_npm(p)
            append_j({"kind": "event", "run_id": run_id, "rule": r, "path": p,
                      "state": "done" if got >= 0 else "failed",
                      "bytes": max(got, 0), "files": n, "info": info,
                      "ts": now_iso()})
            if got >= 0:
                freed += got
                files_done += n
            else:
                failed += 1
            continue
        if k == "tree":
            why = verify_tree_again(p, extra)
            if why:
                append_j({"kind": "event", "run_id": run_id, "rule": r,
                          "path": p, "state": "held", "reason": why,
                          "ts": now_iso()})
                held += 1
                continue
            if extra is None:  # manual user-ordered: journal the baseline
                try:
                    dig, n, tb = tree_digest(p)
                    append_j({"kind": "baseline", "run_id": run_id, "rule": r,
                              "path": p, "tree_sha256": dig, "files": n,
                              "bytes": tb, "ts": now_iso(),
                              "note": "user-ordered deletion 2026-10-19"})
                except (OSError, RuntimeError) as exc:
                    append_j({"kind": "event", "run_id": run_id, "rule": r,
                              "path": p, "state": "held",
                              "reason": "baseline failed: %s" % exc,
                              "ts": now_iso()})
                    held += 1
                    continue
            got, n = purge_tree(p)
            append_j({"kind": "event", "run_id": run_id, "rule": r, "path": p,
                      "state": "done" if n else "failed", "bytes": got,
                      "files": n, "ts": now_iso()})
            freed += got
            files_done += n
            continue
        # plain file
        got = purge_file(p)
        append_j({"kind": "event", "run_id": run_id, "rule": r, "path": p,
                  "state": "done" if got >= 0 else "failed", "bytes": got,
                  "ts": now_iso()})
        if got >= 0:
            freed += got
            files_done += 1
        else:
            failed += 1

    free_after = disk_free()
    append_j({"kind": "summary", "run_id": run_id, "ts": now_iso(),
              "freed_bytes": freed, "files": files_done, "failed": failed,
              "held": held, "free_before": free_before,
              "free_after": free_after,
              "free_delta": {v: free_after[v] - free_before[v]
                             for v in VOLUMES}})
    print("run %s done: freed=%.1f MiB files=%d failed=%d held=%d"
          % (run_id, freed / 1048576.0, files_done, failed, held))
    for v in VOLUMES:
        print("  %s free %.2f -> %.2f GiB (delta %+.2f GiB)"
              % (v, free_before[v] / 2 ** 30, free_after[v] / 2 ** 30,
                 (free_after[v] - free_before[v]) / 2 ** 30))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
