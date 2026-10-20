"""Synthetic fixture + the AC1-AC6 acceptance suite (PRD section 17).

Everything destructive happens inside `.fixture/<runid>/`, never against real
C:/D: content. Deleting a finished fixture tree is done by the out-of-package
`tools/purge_fixture.py`, because this package must not own a delete primitive.
"""
from __future__ import annotations

import ast
import copy
import os
import subprocess
import sys
import time
from typing import Any, Callable, Optional, Sequence

from . import (aiprovider, candidates as cand_mod, config, core,
               evidence as ev_mod, executor as ex_mod, inventory,
               journal as jr_mod, policy as pol_mod, report, tags)

PACKAGE_DIR = core.norm(os.path.dirname(__file__))
PACKAGE_PARENT = core.norm(os.path.dirname(PACKAGE_DIR))
LAYER_A = ("core", "config", "inventory", "evidence", "policy", "tags",
           "candidates", "journal", "index", "aiprovider", "report")
# the acceptance harness and the CLI are the only modules allowed to reach the
# executor; Layer A above must never be able to move or rename anything
EXECUTOR_HARNESS = ("cli", "fixture")
BANNED_CALLS = ("os.remove", "os.unlink", "Path.unlink", "shutil.rmtree",
                "os.replace")
BANNED_TAIL = {b.split(".")[-1] for b in BANNED_CALLS}
BANNED_HEAD = {b.split(".")[0] for b in BANNED_CALLS}
DAY = 86400.0
LONG_TARGET = 245  # > max_target_path (240) but still creatable (< 260)


class FixtureError(Exception):
    pass


# --------------------------------------------------------------------------
# tree construction
# --------------------------------------------------------------------------
def _w(path: str, text: str = "x", *, age_days: float = 0.0,
       size: Optional[int] = None) -> str:
    path = core.norm(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = text if size is None else (text * (1 + max(0, int(size) - len(text)) //
                                               max(1, len(text))))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body or "x")
    if age_days:
        ts = time.time() - age_days * DAY
        os.utime(path, (ts, ts))
    return path


def _make_link(link: str, target: str, notes: list) -> bool:
    try:
        os.makedirs(os.path.dirname(link), exist_ok=True)
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and core.is_reparse(link):
            return True
        notes.append({"mklink_failed": (proc.stdout or proc.stderr)[:120]})
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append({"mklink_error": type(exc).__name__})
    try:
        os.symlink(target, link, target_is_directory=True)
        notes.append({"link_fallback": "symlink"})
        return True
    except OSError as exc:
        notes.append({"symlink_failed": type(exc).__name__})
    return False


def build(base_dir: Optional[str] = None, runid: Optional[str] = None) -> dict:
    base_dir = core.norm(base_dir or os.path.join(PACKAGE_PARENT, ".fixture"))
    runid = runid or "f_%s" % time.strftime("%Y%m%d_%H%M%S")
    root = core.norm(os.path.join(base_dir, runid))
    os.makedirs(root, exist_ok=True)
    notes: list[dict] = []
    fx: dict[str, Any] = {"root": root, "runid": runid, "notes": notes}

    docs = os.path.join(root, "docs")
    _w(os.path.join(docs, "report.pdf"), "%PDF-1.4", age_days=10, size=2048)
    _w(os.path.join(docs, "sheet.xlsx"), "PK", age_days=5, size=1024)
    _w(os.path.join(docs, "photo.jpg"), "JFIF", age_days=2, size=1024)
    _w(os.path.join(docs, "README"), "readme", age_days=1, size=64)
    # already-tagged file (untagged original absent): tag noop + untag target
    fx["tagged"] = _w(os.path.join(docs, "tagged__[类别-文档].pdf"),
                      "%PDF-1.4", age_days=10, size=512)
    # runtime-referenced but rename-safe file, alone in its parent so the
    # referencing-directory prefix marks exactly one candidate
    keepdir = os.path.join(root, "keep")
    fx["keepme"] = _w(os.path.join(keepdir, "keepme.pdf"), "%PDF-1.4", age_days=10,
                      size=512)
    fx["docs"] = docs

    bins = os.path.join(root, "bin")
    _w(os.path.join(bins, "daemon.exe"), "MZ", age_days=40, size=512)
    _w(os.path.join(bins, "lib.dll"), "MZ", age_days=40, size=512)
    fx["bin"] = bins
    _w(os.path.join(root, "src", "build.py"), "print(1)", age_days=3, size=128)
    _w(os.path.join(root, "cfg", "app.json"), "{}", age_days=3, size=128)
    _w(os.path.join(root, "db", "catalog.db"), "SQLI", age_days=3, size=128)
    market = os.path.join(root, "market")
    _w(os.path.join(market, "tick.lc1"), "LC", age_days=200, size=4096)
    _w(os.path.join(market, "tick.lc5"), "LC", age_days=200, size=4096)
    _w(os.path.join(market, "index.day"), "DA", age_days=200, size=4096)
    fx["market"] = market
    cad = os.path.join(root, "cad")
    _w(os.path.join(cad, "bracket.dwg"), "AC", age_days=30, size=512)
    _w(os.path.join(cad, "housing.step"), "IS", age_days=30, size=512)
    fx["cad"] = cad

    # "cache"/"log" in the name but NOT in a regenerable zone: I6 probe
    logs = os.path.join(root, "logs")
    _w(os.path.join(logs, "app-2024.log"), "log line\n", age_days=400, size=5000)
    fx["logs"] = logs
    named = os.path.join(root, "appcache")
    _w(os.path.join(named, "blob.bin"), "bin", age_days=400, size=5000)
    fx["appcache"] = named

    # regenerable zone: the only place an old unreferenced temp may be AUTO_SAFE
    regen = os.path.join(root, "regen_cache")
    _w(os.path.join(regen, "old.tmp"), "stale", age_days=90, size=2048)
    _w(os.path.join(regen, "fresh.tmp"), "new", age_days=1, size=512)
    _w(os.path.join(regen, "nested", "deep-old.tmp"), "stale", age_days=120, size=512)
    fx["regen"] = regen

    # case-difference traps (NTFS is case-insensitive, so use two parents)
    fx["recycle"] = [
        _w(os.path.join(root, "tA", "$RECYCLE.BIN", "hidden_one.txt"), "keep",
           age_days=90),
        _w(os.path.join(root, "tB", "$Recycle.Bin", "hidden_two.txt"), "keep",
           age_days=90)]
    fx["cloud_file"] = _w(os.path.join(root, "我的坚果云", "cloud_only.txt"),
                          "keep", age_days=90)
    frozen = os.path.join(root, "frozen_zone")
    fx["frozen_file"] = _w(os.path.join(frozen, "do_not_touch.pdf"), "keep",
                           age_days=90)
    fx["frozen"] = frozen

    # reparse trap whose target lives outside the scanned root
    outside = core.norm(os.path.join(base_dir, runid + "_outside_target"))
    _w(os.path.join(outside, "through_link.dat"), "never-seen", age_days=90)
    link = os.path.join(root, "link_dir", "via_link")
    fx["link"] = link
    fx["junction_created"] = _make_link(link, outside, notes)
    fx["outside"] = outside
    # real symlink (needs a privilege the junction does not): recorded, not faked
    slink = os.path.join(root, "link_dir", "via_symlink")
    fx["symlink"] = slink
    fx["symlink_created"] = False
    try:
        os.symlink(os.path.join(outside, "through_link.dat"), slink)
        fx["symlink_created"] = os.path.islink(slink)
    except OSError as exc:
        notes.append({"symlink_unavailable": type(exc).__name__})

    # a file whose path is already past the rename length gate but creatable
    stem = "z" * max(1, LONG_TARGET - len(docs) - 5)
    fx["long_file"] = _w(os.path.join(docs, stem + ".pdf"), "x", age_days=10)
    notes.append({"long_file_len": len(core.norm(fx["long_file"]))})

    # collision trap: destination that already exists
    fx["collide_src"] = _w(os.path.join(regen, "collide.tmp"), "old", age_days=90,
                           size=256)
    # rename target clash: photo.jpg tagged 表格 would land on this exact name
    fx["clash"] = _w(os.path.join(docs, "photo__[类别-表格].jpg"), "JFIF",
                     age_days=2, size=256)
    return fx


# --------------------------------------------------------------------------
# policy construction (same builder the TOML path uses)
# --------------------------------------------------------------------------
def flat(fx: dict, **over) -> dict:
    root = fx["root"]
    doc = {
        "policy_id": "FIXTURE", "policy_version": "1",
        "machine": {"python": sys.executable,
                    "state_dir": os.path.join(root, ".state"),
                    "managed_root": os.path.join(root, ".gov-managed"),
                    "strict_interpreter": False, "min_python": [3, 11]},
        "scan": {
            "hard_skip": ["$Recycle.Bin", "System Volume Information", "node_modules",
                          "__pycache__"],
            "cloud_mount_names": ["我的坚果云", "Nutstore", "WPSDrive"],
            "cloud_prefixes": [],
            "frozen_prefixes": [os.path.join(root, "frozen_zone")],
            "regenerable_prefixes": [os.path.join(root, "regen_cache")],
            "protected_prefixes": [], "low_space_pct": 10, "max_actions_per_run": 2000,
        },
        "budget": {"max_wall_s": 120, "max_files": 200000,
                   "max_bytes": 10 * 1024 ** 3},
        "rename": {
            "max_target_path": 240,
            "safe_ext": ["pdf", "xlsx", "jpg", "txt", "md", "zip"],
            "review_ext": ["dwg", "step", "iges"],
            "never_rename_ext": ["exe", "dll", "py", "json", "db", "tmp"],
            "never_touch_ext": ["lc1", "lc5", "day"],
        },
        "tag": {"canonical_group": "类别",
                "categories": ["文档", "表格", "图片", "其他"],
                "category_by_ext": {"pdf": "文档", "xlsx": "表格", "jpg": "图片"}},
        "ai": {"provider": "off", "timeout_s": 5},
        "evidence": {"reparse_roots": [root], "reparse_depth": 3, "timeout_s": 240},
        "scan_root": [{"path": root, "role": "DATA", "purpose": "fixture",
                       "allow_volume_root": False, "kinds": ["scan", "action"]}],
        "rules": [
            {"rule_id": "fx-regen-old-temp", "version": 1, "action_class": "AUTO_SAFE",
             "reason": "regenerable temp, older than 30d, unreferenced",
             "scope_prefixes": [os.path.join(root, "regen_cache")],
             "ext_in": ["tmp"], "min_age_days": 30, "requires_no_reference": True,
             "requires_regenerable": True, "suggested_action": "MOVE",
             "dest_zone": "managed_trash", "evidence_kind": "path_rule"},
            {"rule_id": "fx-name-says-cache", "version": 1,
             "action_class": "REVIEW_REQUIRED", "rank_only": True,
             "reason": "name hint only; never AUTO_SAFE by itself",
             "path_regex": r"(?i)[\\/](logs?|appcache|cache)[\\/]",
             "evidence_kind": "name_hint"},
        ],
    }
    for key, val in over.items():
        if key == "python":
            doc["machine"]["python"] = val
        elif key == "ai_provider":
            doc["ai"]["provider"] = val
        elif key == "max_files":
            doc["budget"]["max_files"] = val
        elif key == "max_target_path":
            doc["rename"]["max_target_path"] = val
        elif key == "regenerable" and not val:
            doc["scan"]["regenerable_prefixes"] = []
        elif key == "ai_cmd":
            doc["ai"]["cmd"] = val
        else:
            doc[key] = val
    return doc


def policy_for(fx: dict, **over) -> config.Policy:
    return config.build(flat(fx, **over), ("<fixture>",),
                        fx["root"], core.machine_name())


def refs_for(fx: dict) -> ev_mod.ReferencedIndex:
    daemon = os.path.join(fx["bin"], "daemon.exe")
    keepme = fx["keepme"]
    return ev_mod.ReferencedIndex.from_rows([
        ev_mod.EvidenceRow("svc", '"%s" -k' % daemon, [daemon]),
        ev_mod.EvidenceRow("cfg", 'open("%s")' % keepme, [keepme])])


def rec_for(path: str, root: str) -> core.Record:
    path = core.norm(path)
    try:
        st = os.lstat(path)
        size, mtime = int(st.st_size), float(st.st_mtime)
    except OSError:
        size, mtime = 10, time.time() - 100 * DAY
    return core.Record.from_entry(scan_id="fx", machine="fx", ts=core.now_iso(),
                                  abspath=path, root=core.norm(root), zone_role="DATA",
                                  st=st if os.path.lexists(path) else _FakeStat(size, mtime),
                                  name=os.path.basename(path), is_dir=False)


class _FakeStat:
    def __init__(self, size, mtime):
        self.st_size, self.st_mtime, self.st_ctime = size, mtime, mtime
        self.st_file_attributes = 0


# --------------------------------------------------------------------------
# result bookkeeping
# --------------------------------------------------------------------------
class Result:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def check(self, name: str, ok: Any, detail: Any = "") -> bool:
        flag = bool(ok)
        self.rows.append({"ac": name.split(" ")[0], "name": name, "ok": flag,
                          "detail": str(detail)[:300]})
        return flag

    def expect_error(self, name: str, fn: Callable, exc: type = Exception) -> bool:
        try:
            fn()
        except exc as err:  # type: ignore[misc]
            return self.check(name, True, "%s: %s" % (type(err).__name__,
                                                      str(err)[:140]))
        except Exception as err:  # noqa: BLE001 - report the wrong type
            return self.check(name, False, "wrong error %s (%s)"
                              % (type(err).__name__, str(err)[:120]))
        return self.check(name, False, "no exception raised")

    @property
    def passed(self) -> int:
        return sum(1 for r in self.rows if r["ok"])

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if not r["ok"]]

    def render(self) -> str:
        lines = ["# acceptance run %s" % core.now_iso()]
        for r in self.rows:
            lines.append("%-4s %-52s %s" % ("PASS" if r["ok"] else "FAIL", r["name"],
                                            r["detail"]))
        lines.append("total=%d pass=%d fail=%d" % (len(self.rows), self.passed,
                                                   len(self.failed)))
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# AC1
# --------------------------------------------------------------------------
def _call_chains(text: str, filename: str = "<string>") -> set:
    """Dotted names this module actually calls, from the AST (no false
    positives on comments, strings or this module's own BANNED_CALLS table)."""
    out = set()
    for node in ast.walk(ast.parse(text, filename)):
        if not isinstance(node, ast.Call):
            continue
        parts, cur = [], node.func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            out.add(".".join(reversed(parts)))
    return out


def _delete_calls(text: str, filename: str = "<string>") -> list:
    """Calls whose head and tail match a delete-capable primitive."""
    bad = {(h, t) for h, t in (b.split(".") for b in BANNED_CALLS)}
    return sorted(c for c in _call_chains(text, filename)
                  if tuple(c.split(".")[-2:]) in bad
                  or (c.split(".")[0], c.split(".")[-1]) in bad)


def _imported_modules(text: str, filename: str = "<string>") -> list:
    """Every module a file imports, including the relative `from . import executor`."""
    names = []
    for node in ast.walk(ast.parse(text, filename)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            prefix = "." * (node.level or 0)
            for a in node.names:
                names.append(prefix + (base + "." + a.name if base else a.name))
    return names


def _imports_executor(text: str, filename: str = "<string>") -> list:
    """Import statements that reach the executor, including `from . import executor`."""
    return [nm for nm in _imported_modules(text, filename) if "executor" in nm]


def ac1_static(res: Result) -> None:
    files = [os.path.join(PACKAGE_DIR, n) for n in sorted(os.listdir(PACKAGE_DIR))
             if n.endswith(".py")]
    hits: list[list] = []
    texts: dict[str, str] = {}
    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            texts[os.path.basename(path)] = fh.read()
    for name, text in sorted(texts.items()):
        for call in _delete_calls(text, name):
            hits.append([name, call])
    # the stricter textual scan stays on the production modules: there the
    # banned words may not appear even in a comment ("a name is not evidence")
    text_hits = [[n, b] for n, t in sorted(texts.items()) if n != "fixture.py"
                 for b in BANNED_CALLS if b in t]
    res.check("AC1 package owns no delete primitive", not hits and not text_hits,
              {"ast": hits, "text": text_hits} or "clean")
    importers = {os.path.splitext(n)[0] for n, t in texts.items()
                 if _imports_executor(t, n)}
    strays = sorted(importers - set(EXECUTOR_HARNESS))
    res.check("AC1 Layer A never imports executor", not strays,
              {"importers": sorted(importers), "strays": strays})
    res.check("AC1 no delete-capable helper inside package",
              not os.path.exists(os.path.join(PACKAGE_DIR, "purge_fixture.py")))
    # positive controls: an assertion that cannot fail proves nothing
    planted = _delete_calls('def f(p):\n    os.remove(p)\n    shutil.rmtree(p)\n')
    res.check("AC1 control: delete scan catches a planted call",
              planted == ["os.remove", "shutil.rmtree"], planted)
    res.check("AC1 fixture module itself is delete-free",
              not _delete_calls(texts["fixture.py"], "fixture.py"))
    caught = _imports_executor("from . import executor\n", "<control>")
    res.check("AC1 control: import scan catches a relative import",
              len(caught) == 1 and caught[0].endswith("executor"), caught)
    caught2 = _imports_executor("from .core import prune_reason\n", "<control>")
    res.check("AC1 control: import scan ignores unrelated imports", not caught2, caught2)


# --------------------------------------------------------------------------
# AC2: the fixture tree carries every trap PRD 17 asks for
# --------------------------------------------------------------------------
def ac2_coverage(res: Result, fx: dict, pol: config.Policy) -> None:
    """PRD 17 AC2: the tree must carry every trap class, and the walk must
    still never see the ones that live behind a prune."""
    recs, _ = _records(pol)
    names = sorted(os.path.basename(r.path) for r in recs)
    seen = {core.low(r.path) for r in recs}
    traps = {
        "recycle bin, both casings": all(os.path.lexists(p) for p in fx["recycle"]),
        "junction": fx["junction_created"] is True,
        "symlink or recorded privilege gap": (fx["symlink_created"] or any(
            "symlink_unavailable" in n for n in fx["notes"])),
        "cloud mount name": os.path.lexists(fx["cloud_file"]),
        "frozen zone": os.path.lexists(fx["frozen_file"]),
        "name collision": os.path.lexists(fx["clash"]) and os.path.lexists(fx["collide_src"]),
        "over-long path": len(core.norm(fx["long_file"])) > pol.max_target_path,
        "never_touch ext": any(n.endswith(".lc1") for n in names),
        "never_rename ext": any(n.endswith(".exe") for n in names),
        "review ext (CAD/STEP)": any(n.endswith((".dwg", ".step")) for n in names),
        "extensionless": "README" in names,
        "runtime-referenced exe": "daemon.exe" in names,
        "regenerable temp": any(n.endswith(".tmp") for n in names),
    }
    missing = sorted(k for k, ok in traps.items() if not ok)
    invisible = [fx["cloud_file"], fx["frozen_file"]] + list(fx["recycle"])
    res.check("AC2 fixture carries every PRD trap", not missing,
              {"missing": missing, "files": len(names)})
    res.check("AC2 the pruned traps stay invisible to the walk",
              not any(core.low(p) in seen for p in invisible),
              [os.path.basename(p) for p in invisible if core.low(p) in seen])
    res.check("AC2 fixture size stays synthetic (20-80 files)",
              20 <= len(names) <= 80, len(names))


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------
def _records(pol: config.Policy) -> tuple[list[core.Record], inventory.ScanStats]:
    out: list[core.Record] = []
    stats = inventory.walk(pol.roots[0].path, rules=pol, budget=pol.new_budget(),
                          emit=out.append)
    return out, stats


def ac_inventory(res: Result, fx: dict, pol: config.Policy) -> None:
    recs, stats = _records(pol)
    paths = {core.low(r.path) for r in recs}
    res.check("AC3 recycle bin (both casings) not descended",
              not any("hidden_one" in p or "hidden_two" in p for p in paths))
    res.check("AC3 cloud mount not descended", not any("cloud_only" in p for p in paths))
    res.check("AC3 reparse link not descended",
              not any("through_link" in p for p in paths),
              "link=%s junction_created=%s" % (core.is_reparse(os.path.join(
                  fx["root"], "link_dir", "via_link")), fx["junction_created"]))
    res.check("AC3 link itself is recorded as reparse",
              fx["junction_created"] is True and stats.prunes.get("reparse", 0) >= 1,
              {"junction": fx["junction_created"], "prunes": stats.prunes})
    res.check("AC3 symlink reported as reparse, never followed",
              any(core.low(fx["symlink"]) == core.low(p[0]) for p in stats.reparse)
              if fx["symlink_created"] else True,
              {"symlink": fx["symlink_created"], "reparse": stats.reparse[:3]})
    res.check("AC3 hard_skip recorded", stats.prunes.get("hard_skip", 0) >= 2,
              stats.prunes)
    res.check("AC3 cloud prune recorded", stats.prunes.get("cloud_mount", 0) >= 1,
              stats.prunes)
    res.check("AC3 frozen zone recorded but not scanned",
              stats.prunes.get("frozen_zone", 0) >= 1 and
              not any("do_not_touch" in p for p in paths), stats.prunes)
    file_recs = [r for r in recs if not r.is_dir]
    res.check("FR1 snapshot records exactly what the stats report",
              stats.files == len(file_recs) and
              len([r for r in recs if r.is_reparse]) == len(stats.reparse),
              {"files": stats.files, "records": len(recs), "reparse": len(stats.reparse)})
    res.check("AC3 denied paths reported not hidden", isinstance(stats.denied, list),
              len(stats.denied))
    res.check("FR12 snapshot roundtrip", _snapshot_roundtrip(fx, pol, recs, stats))
    res.check("FR12 toplevel diff keeps the root out of its own listing",
              _toplevel_probe(pol))
    res.check("FR2 budget hit stops the walk and is reported", _budget_hit(pol))


def _toplevel_probe(pol: config.Policy) -> bool:
    """FR12: the root itself is not one of its own top-level directories."""
    root = core.norm(pol.roots[0].path)
    dirs = {root: inventory.Rollup(files=1, bytes=1),
            os.path.join(root, "docs"): inventory.Rollup(files=1, bytes=1)}
    got = inventory.diff_snapshot({"dirs": dirs}, dirs, [root])["toplevel"][root]
    same = inventory.toplevel_dirs(dirs, root) == {os.path.join(root, "docs")}
    return got == {"new": [], "gone": []} and same


def _snapshot_roundtrip(fx: dict, pol: config.Policy, recs, stats) -> bool:
    dirs, st2 = inventory.rollup([pol.roots[0].path], rules=pol)
    path = inventory.save_snapshot(pol.state_dir, stats.scan_id, dirs, st2)
    snap = inventory.load_snapshot(path)
    head = inventory.snapshot_header(path)
    if not snap["dirs"]:
        return False
    d = inventory.diff_snapshot(snap, dirs, [pol.roots[0].path])
    return (all(k in d for k in ("added", "removed", "grew", "toplevel"))
            and snap["dirs"] and not d["added"]
            and bool(head.get("toplevel"))
            and bool((head.get("stats") or {}).get("roots")))


def _scan_probe(subroot: str, pol: config.Policy):
    """Run cli.Cli.scan (the `--root` path) against a synthetic policy.

    `__new__` skips the interpreter self-check in __init__; scan() itself only
    needs the policy. Returns (record paths, stats).
    """
    cli_mod = __import__("gov.cli", fromlist=["Cli"])
    probe = cli_mod.Cli.__new__(cli_mod.Cli)
    probe.pol = pol
    recs, stats = cli_mod.Cli.scan(probe, [subroot])
    return [r.path for r in recs], stats


def ac_scan_scope(res: Result, fx: dict, pol: config.Policy) -> None:
    """Regressions found by the first real two-volume dry run (2026-09-22)."""
    root = core.norm(pol.roots[0].path)
    sub = os.path.join(root, "docs")
    paths, stats = _scan_probe(sub, pol)
    sub_l = core.low(sub) + os.sep
    res.check("FR1 --root <subdir> scans the subdir, not the whole volume",
              paths and all(core.low(p).startswith(sub_l) for p in paths)
              and stats.roots == [sub],
              {"records": len(paths), "roots": stats.roots,
               "outside": [p for p in paths if not core.low(p).startswith(sub_l)][:3]})
    outside = os.path.join(os.path.dirname(root), "not_a_declared_root")
    res.expect_error("FR1 undeclared --root refused",
                     lambda: _scan_probe(outside, pol), config.RootError)
    # a volume root already ends in a separator: "d:\" + "\" matched no path, so
    # the rollup printed exactly one row
    vol = "Q:\\"
    dirs = {vol: inventory.Rollup(own_files=0, own_bytes=0, files=3, bytes=15),
            "Q:\\a": inventory.Rollup(files=2, bytes=10),
            "Q:\\a\\b": inventory.Rollup(files=1, bytes=5)}
    body = [ln for ln in report.render_rollup(dirs, vol).splitlines()[1:]
            if ln.strip()]
    res.check("FR12 rollup lists children of a volume root",
              len(body) == 3 and all(any(ln.endswith(p) for ln in body)
                                     for p in (vol, "Q:\\a", "Q:\\a\\b")),
              body)
    newest = inventory.latest_snapshot(pol.state_dir)
    res.check("FR12 baseline snapshot must cover the requested root",
              bool(newest) and
              inventory.latest_snapshot(pol.state_dir, roots=[root]) == newest and
              inventory.latest_snapshot(pol.state_dir,
                                        roots=["Z:\\never-scanned"]) is None,
              {"newest": newest})


def _budget_hit(pol: config.Policy) -> bool:
    full = len(_records(pol)[0])
    tight = core.Budget(max_wall_s=60, max_files=3, max_bytes=10 ** 15)
    recs: list[core.Record] = []
    stats = inventory.walk(pol.roots[0].path, rules=pol, budget=tight, emit=recs.append)
    return (stats.budget_hit == "max_files" and len(tight.hits) == 1
            and stats.files == tight.files and 1 <= stats.files < full)


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------
def ac_policy(res: Result, fx: dict, pol: config.Policy, refs) -> None:
    def dec(rel: str) -> pol_mod.Decision:
        return pol_mod.classify(rec_for(os.path.join(fx["root"], rel), fx["root"]),
                                refs, pol)

    d = dec(os.path.join("bin", "daemon.exe"))
    res.check("AC3 referenced exe is PROTECTED", d.risk_class == "PROTECTED", d.as_dict())
    for rel in ("market\\tick.lc1", "market\\tick.lc5", "market\\index.day"):
        d = dec(rel)
        res.check("AC3 market data %s NEVER_TOUCH" % os.path.splitext(rel)[1],
                  d.risk_class == "NEVER_TOUCH", d.risk_class)
    d = dec(os.path.join("logs", "app-2024.log"))
    res.check("AC3 log-named old file is not AUTO_SAFE", d.risk_class != "AUTO_SAFE",
              d.as_dict())
    d = dec(os.path.join("appcache", "blob.bin"))
    res.check("AC3 cache-named old file is not AUTO_SAFE", d.risk_class != "AUTO_SAFE",
              d.as_dict())
    d = dec(os.path.join("regen_cache", "old.tmp"))
    res.check("AC3 regenerable+old+unreferenced is AUTO_SAFE",
              d.risk_class == "AUTO_SAFE", d.as_dict())
    d = dec(os.path.join("regen_cache", "nested", "deep-old.tmp"))
    res.check("AC3 AUTO_SAFE inherits scope from subdirectory",
              d.risk_class == "AUTO_SAFE", d.rule_ids)
    d = dec(os.path.join("regen_cache", "fresh.tmp"))
    res.check("AC3 young file fails the age gate", d.risk_class == "REVIEW_REQUIRED",
              d.evidence.get("gate_failed"))
    pol_noregen = policy_for(fx, regenerable=False)
    d = pol_mod.classify(rec_for(os.path.join(fx["root"], "regen_cache", "old.tmp"),
                                 fx["root"]), refs, pol_noregen)
    res.check("I6 AUTO_SAFE refused when zone is not regenerable",
              d.risk_class == "REVIEW_REQUIRED", d.evidence.get("gate_failed"))

    def dec_ref_by(kind: str, rel: str):
        """Classify with only a container-level reference on the fixture root."""
        coarse = ev_mod.ReferencedIndex.from_rows(
            [ev_mod.EvidenceRow(kind, "X=%s" % fx["root"], [fx["root"]])])
        return pol_mod.classify(rec_for(os.path.join(fx["root"], rel), fx["root"]),
                                coarse, pol)

    old_tmp = os.path.join("regen_cache", "old.tmp")
    d = dec_ref_by("env", old_tmp)
    res.check("FR4 container inference does not swallow a declared zone",
              d.risk_class == "AUTO_SAFE" and
              d.evidence.get("container_reference_ignored") == ["env"], d.as_dict())
    d = dec_ref_by("svc", old_tmp)
    res.check("FR4 runtime reference still blocks even a declared zone",
              d.risk_class == "PROTECTED", d.as_dict())
    d = dec_ref_by("env", os.path.join("docs", "report.pdf"))
    res.check("FR4 the override is bounded by the declared zone",
              d.risk_class == "PROTECTED", d.as_dict())
    d = dec(os.path.join("docs", "report.pdf"))
    res.check("AC3 document is review-only", d.risk_class == "REVIEW_REQUIRED",
              d.as_dict())
    d = dec(os.path.join("frozen_zone", "do_not_touch.pdf"))
    res.check("AC3 frozen zone is NEVER_TOUCH", d.risk_class == "NEVER_TOUCH",
              d.as_dict())
    d = dec(os.path.join("cad", "bracket.dwg"))
    res.check("AC3 CAD tier is TAG_WITH_REVIEW", d.rename_safety == "TAG_WITH_REVIEW",
              d.rename_safety)
    d = dec(os.path.join("docs", "README"))
    res.check("AC3 extensionless is NEVER_RENAME", d.rename_safety == "NEVER_RENAME",
              d.rename_safety)
    d = pol_mod.classify(rec_for(os.path.join(fx["root"], "weird.zzz"), fx["root"]),
                         refs, pol)
    res.check("I7 unknown extension is never deletable", d.risk_class != "AUTO_SAFE",
              d.as_dict())
    res.check("I7 unknown extension flagged unknown", d.unknown is True)


# --------------------------------------------------------------------------
# tags
# --------------------------------------------------------------------------
def ac_tags(res: Result, fx: dict, pol: config.Policy, refs) -> None:
    T = [tags.Tag("类别", "文档")]
    name = tags.format_name("report", ".pdf", T)
    _, base, ext, parsed = tags.parse_path(os.path.join(fx["docs"], name))
    res.check("AC3 tag format/parse roundtrip", tags.format_name(base, ext, parsed) == name,
              name)
    res.check("AC3 tag apply is idempotent",
              tags.format_name(base, ext, parsed + parsed) == name)
    res.check("AC3 bracketed non-tag text is preserved",
              tags.parse_name("my__file[1].pdf")[0] == "my__file[1]")
    res.check("AC3 original basename kept intact", base == "report", base)
    src = os.path.join(fx["docs"], "report.pdf")
    tgt, rej = tags.plan_rename(src, T, pol, refs)
    res.check("AC3 safe file yields a rename plan", bool(tgt) and rej is None, [tgt, rej])
    ntgt, rej = tags.plan_rename(fx["tagged"], T, pol, refs)
    res.check("AC3 tagging an already-tagged file is a noop",
              rej == "noop" and ntgt is None, [ntgt, rej])
    _, rej = tags.plan_rename(os.path.join(fx["bin"], "daemon.exe"), T, pol, refs)
    res.check("I4 NEVER_RENAME extension refused", rej == "never_rename", rej)
    _, rej = tags.plan_rename(os.path.join(fx["cad"], "bracket.dwg"), T, pol, refs)
    res.check("I4 TAG_WITH_REVIEW refused without approval", rej == "review_only", rej)
    _, rej = tags.plan_rename(fx["keepme"], T, pol, refs, approved=True)
    res.check("AC3 referenced file refused even when approved", rej == "referenced", rej)
    _, rej = tags.plan_rename(os.path.join(fx["frozen"], "do_not_touch.pdf"), T, pol, refs)
    res.check("AC3 frozen zone refused", rej == "frozen_zone", rej)
    _, rej = tags.plan_rename(fx["long_file"], T * 3, pol, refs)
    res.check("C4 length gate fails closed (no truncation)", rej == "too_long",
              [len(fx["long_file"]), rej])
    _, rej = tags.plan_rename(os.path.join(fx["docs"], "photo.jpg"),
                              [tags.Tag("类别", "表格")], pol, refs)
    res.check("AC3 name collision refused", rej == "collision", rej)
    unt = tags.plan_untag(fx["tagged"], pol, refs, tier="FILENAME_TAG_SAFE")
    res.check("AC3 untag restores the original name",
              os.path.basename(unt[0] or "") == "tagged.pdf", unt)
    res.check("FR8 category comes from policy not code",
              tags.category_for("xlsx", pol) == tags.Tag("类别", "表格"),
              pol.tag.get("category_by_ext", {}).keys())


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------
def ac_candidates(res: Result, fx: dict, pol: config.Policy, refs) -> list:
    recs, _ = _records(pol)
    cands = list(cand_mod.build(recs, refs, pol))
    res.check("FR5 every candidate carries evidence", bool(cands) and
              all(c.evidence for c in cands), len(cands))
    auto = [c for c in cands if c.risk_class == "AUTO_SAFE"]
    res.check("FR5 AUTO_SAFE candidates pass all three gates",
              all(c.evidence.get("age_ok") and c.evidence.get("no_reference_ok")
                  and c.evidence.get("regenerable_ok") for c in auto),
              [os.path.basename(c.path) for c in auto])
    res.check("FR5 no candidate is UNKNOWN-but-deletable",
              not any(c.unknown and c.risk_class == "AUTO_SAFE" for c in cands))
    res.check("AC6 provider=off adds no ai payload", all(c.ai is None for c in cands))
    bad = cand_mod.Candidate(os.path.join(fx["root"], "x.pdf"), 1, time.time(), "pdf",
                             "AUTO_SAFE", "FILENAME_TAG_SAFE", "MOVE", ["r"], {})
    res.expect_error("FR5 candidate without evidence raises", bad.validate,
                     cand_mod.CandidateError)
    res.check("FR5 summary buckets by risk and rule",
              set(cand_mod.summarize(cands)) <= set(core.RISK_CLASSES))
    return cands


# --------------------------------------------------------------------------
# apply / undo / journal
# --------------------------------------------------------------------------
def ac_apply(res: Result, fx: dict, pol: config.Policy, refs, cands) -> None:
    root = pol.roots[0].path
    jr = jr_mod.Journal(pol.state_dir)
    _, blocked = ex_mod.plan(pol, cands, kind="MOVE", roots=[root], refs=refs,
                             approved_ids=set())
    res.check("FR6 plan marks unapproved paths blocked",
              all(a.reject == "not_approved" for a in blocked if a.action == "MOVE"),
              [a.reject for a in blocked][:4])
    res.expect_error("FR6 apply without the approval flag raises",
                     lambda: ex_mod.apply_plan(pol, blocked, jr, approved=False),
                     ex_mod.ExecuteError)
    plan_id, actions = ex_mod.plan(pol, cands, kind="MOVE", roots=[root], refs=refs)
    go = [a for a in actions if not a.reject]
    res.check("FR6 plan produced actionable moves", len(go) >= 2, len(go))
    auto_paths = {core.norm(c.path) for c in cands if c.risk_class == "AUTO_SAFE"}
    res.check("FR6 only AUTO_SAFE paths are actionable",
              all(core.norm(a.src) in auto_paths for a in go))
    res.check("FR6 destinations stay inside the managed root",
              all(core.low(a.dst).startswith(core.low(pol.managed_root)) for a in go))
    before = {core.low(p) for p in _all_paths(root, pol)}
    mf_pre, meta_pre = report.manifest(root, pol)
    mf_again, _ = report.manifest(root, pol)
    res.check("AC4 read-only manifest is reproducible",
              report.manifest_diff(mf_pre, mf_again)["identical"],
              {"meta": meta_pre,
               "diff": report.manifest_diff(mf_pre, mf_again)})
    out = ex_mod.apply_plan(pol, actions, jr, approved=True)
    res.check("FR6 apply moved everything actionable", out["done"] == len(go), out)
    res.check("FR6 moved files exist in managed trash",
              all(os.path.lexists(a.dst) for a in go))
    after = {core.low(p) for p in _all_paths(root, pol)}
    mf_post, _ = report.manifest(root, pol)
    diff = report.manifest_diff(mf_pre, mf_post)
    res.check("AC4 manifest is sensitive to a real apply",
              not diff["identical"] and diff["paths_removed"] == len(go), diff)
    mf_now, _ = report.manifest(root, pol)
    mf_excl, meta_excl = report.manifest(root, pol, exclude=[os.path.join(root, "docs")])
    res.check("AC4 proof exclusion is disclosed, not silent",
              meta_pre["excluded_files"] == 0 and meta_excl["excluded_files"] > 0 and
              len(mf_now) - len(mf_excl) == meta_excl["excluded_files"],
              {"no_exclusion": meta_pre["excluded_files"],
               "with_exclusion": meta_excl["excluded_files"],
               "rows": [len(mf_now), len(mf_excl)]})
    moved_src = {core.low(a.src) for a in go}
    unexplained = sorted((before - moved_src) - after)
    res.check("C1 zero unauthorized permanent deletion",
              not unexplained and all(core.low(a.src) not in after for a in go) and
              all(core.low(a.dst) in after for a in go),
              {"unexplained": unexplained[:3],
               "moved": sorted(os.path.basename(s) for s in moved_src)[:3]})
    res.check("FR9 journal has planned+done per event",
              all(jr.get(a.event_id)["state"] == "done" for a in go))
    res.check("FR9 journal fields complete (PRD 13)",
              all(k in jr.get(go[0].event_id) for k in jr_mod.EVENT_FIELDS),
              sorted(set(jr_mod.EVENT_FIELDS) - set(jr.get(go[0].event_id) or {})))
    res.check("FR6 sidecar metadata written next to trash item",
              all(os.path.lexists(os.path.join(os.path.dirname(a.dst), ".gov_meta.json"))
                  for a in go))
    res.check("AC3 no crash residue after a clean run", not jr.unfinished(),
              [e.get("event_id") for e in jr.unfinished()][:3])
    undo_out = [ex_mod.undo_event(pol, jr, a.event_id, approved=True) for a in go]
    res.check("FR9 undo reports ok for each", all(u["ok"] for u in undo_out), undo_out[:2])
    restored = {core.low(p) for p in _all_paths(root, pol)}
    res.check("FR9 undo restores 100 percent of originals",
              all(core.low(a.src) in restored for a in go),
              [os.path.basename(a.src) for a in go if core.low(a.src) not in restored])
    res.check("FR9 double undo refused",
              not ex_mod.undo_event(pol, jr, go[0].event_id, approved=True)["ok"],
              ex_mod.undo_event(pol, jr, go[0].event_id, approved=True))
    jr.plan({"event_id": "e_crash_sim", "ts": core.now_iso(), "machine": "fx",
             "actor": "fixture", "plan_id": "p_crash", "action": "MOVE",
             "src": os.path.join(root, "nowhere"), "dst": os.path.join(root, "nowhere2"),
             "src_hash": None, "size": 0, "mtime": 0.0, "risk_class": "AUTO_SAFE",
             "rule_ids": [], "policy_id": "fx", "policy_version": "1", "evidence": {},
             "ai": None, "state": "planned", "undo_event_id": None})
    res.check("I5 crash residue surfaces in health",
              any(e.get("event_id") == "e_crash_sim" for e in jr.unfinished()),
              jr.summary())
    res.expect_error("FR9 journal refuses an event missing fields",
                     lambda: jr.append({"event_id": "e_x"}), jr_mod.JournalError)
    # collision on MOVE
    collide = ex_mod.Action(plan_id, "e_collide", "MOVE", fx["collide_src"],
                            fx["collide_src"], "AUTO_SAFE", [], "test", 0, 0.0)
    collide.reject = ex_mod._move_reject(collide, pol, refs)
    res.check("FR7 name collision blocks the move (no suffix added)",
              collide.reject == "collision", collide.reject)
    cross = ex_mod.Action(plan_id, "e_cross", "MOVE", fx["collide_src"],
                          r"F:\ghost\collide.tmp", "AUTO_SAFE", [], "test", 0, 0.0)
    cross.reject = ex_mod._move_reject(cross, pol, refs)
    res.check("FR7 cross-volume move refused in plan", cross.reject == "cross_volume",
              cross.reject)
    raw = list(jr.iter_lines())
    counts = jr.summary()
    chain = [r.get("state") for r in raw if r.get("event_id") == go[0].event_id]
    res.check("FR9 journal keeps the whole chain (append-only states)",
              len(raw) > counts["events"] and chain == ["planned", "done", "undone"],
              {"lines": len(raw), "events": counts["events"], "chain": chain})
    res.check("FR9 summary counts events, not lines",
              counts["events"] == len({r.get("event_id") for r in raw}) and
              counts["by_state"]["undone"] >= len(go), counts)


def _all_paths(root: str, pol: config.Policy) -> list[str]:
    out: list[str] = []
    inventory.walk(root, rules=pol, budget=core.Budget(max_wall_s=120,
                                                        max_files=200000,
                                                        max_bytes=10 ** 15),
                   emit=lambda r: out.append(r.path))
    for base, _dirs, names in os.walk(os.path.join(root, ".gov-managed")):
        out.extend(os.path.join(base, n) for n in names)
    return out


# --------------------------------------------------------------------------
# config semantics
# --------------------------------------------------------------------------
def ac_config(res: Result, fx: dict, pol: config.Policy) -> None:
    base = flat(fx)
    res.check("PRD14 fixture policy loaded", pol.policy_id == "FIXTURE", pol.source_files)
    res.expect_error("I3 AUTO_SAFE rule without gates is rejected at load",
                     lambda: config.build(_rule_no_gates(fx), ("<fx>",),
                                          fx["root"], "x"),
                     config.PolicyError)
    res.expect_error("I3 AUTO_SAFE rule without no_reference is rejected",
                     lambda: config.build(_rule_no_ref(fx), ("<fx>",),
                                          fx["root"], "x"), config.PolicyError)
    res.expect_error("PRD14 unknown rule field rejected (typo cannot widen scope)",
                     lambda: config.build(_rule_typo(fx), ("<fx>",),
                                          fx["root"], "x"), config.PolicyError)
    merged = config._merge(base, {
        "rules": [dict(base["rules"][0], reason="replaced", min_age_days=60)]}, "role")
    res.check("PRD14 rule_id replaces the whole entry",
              merged["rules"][0]["reason"] == "replaced" and
              merged["rules"][0]["min_age_days"] == 60, merged["rules"][0])
    res.expect_error("PRD14 severity change across layers is refused",
                     lambda: config._merge(base, {
                         "rules": [dict(base["rules"][0], action_class="NEVER_TOUCH")]},
                         "role"), config.PolicyError)
    res.expect_error("PRD14 plain list redefinition refused",
                     lambda: config._merge(base, {"scan": {"hard_skip": ["only_this"]}},
                                           "machine"), config.PolicyError)
    victim = base["scan"]["hard_skip"][0]
    directed = config._merge(base, {"scan": {"hard_skip_add": ["ExtraSkip"],
                                             "hard_skip_remove": [victim]}}, "role")
    res.check("PRD14 add/remove directives applied",
              "ExtraSkip" in directed["scan"]["hard_skip"] and
              victim not in directed["scan"]["hard_skip"],
              {"removed": victim, "list": directed["scan"]["hard_skip"]})
    res.expect_error("PRD14 a directive for an undeclared list is refused",
                     lambda: config._merge(base, {"scan": {"nothing_here_add": ["x"]}},
                                           "role"), config.PolicyError)
    res.expect_error("PRD14 _remove may not target a key that does not exist",
                     lambda: config._merge(base, {"scan": {"never_mentioned_remove":
                                                           ["x"]}}, "role"),
                     config.PolicyError)
    res.expect_error("PRD14 machine may not widen a max_*",
                     lambda: config._check_loosening(
                         base, {"budget": {"max_files": base["budget"]["max_files"] + 1}},
                         "machine"), config.PolicyError)
    res.expect_error("PRD14 machine may not add a new AUTO_SAFE rule",
                     lambda: config._check_loosening(
                         base, {"rules": [{"rule_id": "new-loose",
                                           "action_class": "AUTO_SAFE"}]}, "machine"),
                     config.PolicyError)
    tightened = config._merge(base, {"rename": {"never_touch_ext_add": ["sqlite"]}},
                              "machine")
    res.check("PRD14 machine layer may tighten lists",
              "sqlite" in tightened["rename"]["never_touch_ext"], "")
    res.expect_error("FR14 missing interpreter path aborts",
                     lambda: config.check_interpreter(policy_for(fx, python=r"Z:\nope.exe")),
                     config.PolicyError)
    res.check("FR14 configured interpreter accepted",
              config.check_interpreter(pol) == sys.executable, pol.python)
    res.check("I3 action on an undeclared root refused",
              pol.action_target_ok(r"D:\Windows") is not None,
              pol.action_target_ok(r"D:\Windows"))
    vol_pol = policy_for(fx, scan_root=[
        {"path": fx["root"], "role": "DATA", "purpose": "fixture",
         "allow_volume_root": False, "kinds": ["scan", "action"]},
        {"path": "Z:\\", "role": "DATA", "purpose": "volume-root probe",
         "allow_volume_root": True, "kinds": ["scan", "action"]}])
    res.check("I3 volume root may not be an action target",
              vol_pol.action_target_ok("Z:\\") == "volume_root_target",
              vol_pol.action_target_ok("Z:\\"))
    res.check("I3 volume root stays usable for read-only observation",
              vol_pol.authorize("Z:\\", "scan").is_volume_root is True)
    res.check("I3 an undeclared volume root is refused before any action",
              str(pol.action_target_ok("C:\\")).startswith("root not declared"),
              pol.action_target_ok("C:\\"))
    res.check("I3 declared fixture root is actionable",
              pol.action_target_ok(fx["docs"]) is None)
    res.expect_error("I3 cloud mount as scan root refused",
                     lambda: pol.authorize(os.path.join(fx["root"], "我的坚果云"), "scan"),
                     config.RootError)
    res.expect_error("I3 frozen zone as scan root refused",
                     lambda: pol.authorize(fx["frozen"], "scan"), config.RootError)
    res.check("I3 scan of a sibling inside a frozen tree still prunes",
              core.prune_reason("x", os.path.join(fx["frozen"], "x"), 0, pol) == "frozen_zone")


def _rule_no_gates(fx: dict) -> dict:
    doc = copy.deepcopy(flat(fx))
    doc["rules"].append({"rule_id": "bad-no-gates", "version": 1,
                         "action_class": "AUTO_SAFE", "reason": "name only",
                         "ext_in": ["lc1"]})
    return doc


def _rule_no_ref(fx: dict) -> dict:
    doc = copy.deepcopy(flat(fx))
    doc["rules"].append({"rule_id": "bad-no-ref", "version": 1,
                         "action_class": "AUTO_SAFE", "reason": "age but no ref gate",
                         "min_age_days": 30, "requires_regenerable": True})
    return doc


def _rule_typo(fx: dict) -> dict:
    doc = copy.deepcopy(flat(fx))
    doc["rules"].append({"rule_id": "bad-typo", "version": 1, "action_class": "REVIEW",
                         "reason": "x", "scope_prefix": [fx["root"]]})
    return doc


# --------------------------------------------------------------------------
# AI layer
# --------------------------------------------------------------------------
def ac_ai(res: Result, fx: dict, pol: config.Policy) -> None:
    res.check("AC6 off provider returns None (no subprocess, no network)",
              aiprovider.judge({"path": r"C:\x\a.pdf", "ext": "pdf", "size": 1,
                                "age_days": 1}, {}, pol) is None, pol.ai.get("provider"))
    missing = policy_for(fx, ai_provider="local_cli",
                         ai_cmd=["definitely-not-a-real-binary-9f3"])
    out = aiprovider.judge({"path": r"C:\x\a.pdf", "ext": "pdf", "size": 1,
                            "age_days": 1}, {}, missing)
    res.check("FR13 unavailable ai fails closed with an error payload",
              isinstance(out, dict) and "error" in out, out)
    bad_json = aiprovider.extract_json("prose before {\"a\": 1} prose after")
    res.check("FR13 tolerant json extraction", bad_json == {"a": 1}, bad_json)
    res.check("FR13 non-json output yields None",
              aiprovider.extract_json("no object here") is None)
    good = {"category": "文档", "tags": ["类别-文档"], "project_or_topic": "",
            "rename_safety": "FILENAME_TAG_SAFE", "confidence": 0.9, "reason": "x",
            "suggested_action": "NONE"}
    res.check("FR13 strict 7-key judgment accepted",
              aiprovider._validate(good, ("文档",)) is not None)
    res.check("FR13 extra key rejected",
              aiprovider._validate(dict(good, extra=1), ("文档",)) is None)
    res.check("FR13 out-of-enum category rejected",
              aiprovider._validate(dict(good, category="nonsense"), ("文档",)) is None)
    res.check("FR13 confidence range enforced",
              aiprovider._validate(dict(good, confidence=1.5), ("文档",)) is None)
    res.check("FR13 tags must be a list of strings",
              aiprovider._validate(dict(good, tags="类别-文档"), ("文档",)) is None)
    res.check("FR13 prompt asks for exactly the seven keys",
              set(aiprovider.JUDGMENT_KEYS) == {"category", "tags",
                                                "project_or_topic", "rename_safety",
                                                "confidence", "reason",
                                                "suggested_action"})
    sent = aiprovider.desensitize({"path": r"C:\Users\example\Documents\a.pdf"})
    res.check("FR13 ai input carries no drive or user directory",
              "Users" not in str(sent) and "C:" not in str(sent), sent)
    res.check("FR13 preview withheld unless explicitly allowed",
              "preview" not in aiprovider.desensitize({"path": "a/b.pdf",
                                                       "preview": "secret body"}))
    res.check("FR13 preview capped at 512 bytes when allowed",
              len(aiprovider.desensitize({"path": "a/b.pdf", "preview": "y" * 900},
                                         allow_preview=True)["preview"]) <= 512)
    docs = pol_mod.classify(rec_for(os.path.join(fx["docs"], "report.pdf"), fx["root"]),
                            None, pol)
    res.check("FR13 known documents are never sent to ai", not pol_mod.needs_ai(docs),
              docs.risk_class)
    unknown = pol_mod.classify(rec_for(os.path.join(fx["root"], "weird.zzz"), fx["root"]),
                               None, pol)
    res.check("FR13 unknown records are the ai candidates", pol_mod.needs_ai(unknown),
              unknown.as_dict())


# --------------------------------------------------------------------------
# performance and index
# --------------------------------------------------------------------------
def ac_perf(res: Result, fx: dict, pol: config.Policy) -> None:
    t0 = time.monotonic()
    inventory.top_shallow(os.path.dirname(fx["root"]), rules=pol)
    res.check("AC5 shallow parent listing < 1000 ms", time.monotonic() - t0 < 1.0,
              "%.1f ms" % ((time.monotonic() - t0) * 1000))
    t0 = time.monotonic()
    recs, stats = _records(pol)
    rate = stats.files / max(stats.wall_s, 0.001)
    res.check("AC5 scan throughput >= 3000 files/s", rate >= 3000,
              "%.0f files/s over %d files in %.3f s" % (rate, stats.files,
                                                       stats.wall_s))
    free = inventory.volume_free("C:\\")
    res.check("AC5 volume stats readable", "error" not in free, free)
    idx_path = os.path.join(pol.state_dir, "index", "catalog.sqlite")
    idx = __import__("gov.index", fromlist=["Index"]).Index(pol.state_dir)
    snap = inventory.latest_snapshot(pol.state_dir) or ""
    counts = idx.rebuild(inventory_paths=[], snapshot_paths=[snap] if snap else [],
                         journal_lines=jr_mod.Journal(pol.state_dir).all_events())
    res.check("FR10 index rebuilt from NDJSON", counts["actions"] >= 2, counts)
    res.check("FR10 index file present", os.path.isfile(idx_path))
    res.check("FR10 index queries work",
              isinstance(idx.actions(), list) and idx.top_dirs(5) is not None)


def ac_journal_chain(res: Result, fx: dict, pol: config.Policy) -> None:
    """FR10: the SQLite index is disposable; the NDJSON journal is the authority."""
    jr = jr_mod.Journal(pol.state_dir)
    idxmod = __import__("gov.index", fromlist=["Index"])
    before = jr.all_events()
    idx = idxmod.Index(pol.state_dir)
    idx.rebuild(journal_lines=before)
    with idx.connect() as conn:
        rows = conn.execute("SELECT event_id,state FROM actions").fetchall()
    res.check("FR10 index mirrors the journal", len(rows) >= len(before),
              {"journal": len(before), "index": len(rows)})
    rebuilt = jr.all_events()
    res.check("FR10 journal untouched by index rebuild",
              [e["event_id"] for e in rebuilt] == [e["event_id"] for e in before])


def run_acceptance(base_dir: Optional[str] = None, keep: bool = True) -> Result:
    res = Result()
    ac1_static(res)
    fx = build(base_dir)
    pol = policy_for(fx)
    refs = refs_for(fx)
    try:
        ac_inventory(res, fx, pol)
        ac2_coverage(res, fx, pol)
        ac_scan_scope(res, fx, pol)
        ac_policy(res, fx, pol, refs)
        ac_tags(res, fx, pol, refs)
        cands = ac_candidates(res, fx, pol, refs)
        ac_apply(res, fx, pol, refs, cands)
        ac_config(res, fx, pol)
        ac_ai(res, fx, pol)
        ac_perf(res, fx, pol)
        ac_journal_chain(res, fx, pol)
        res.check("FR11 root role reported", pol.roots[0].role == "DATA", pol.roots[0])
        res.check("FR11 unknown toplevel detection available",
                  "toplevel" in inventory.diff_snapshot({"dirs": {}},
                                                        {os.path.join(fx["root"], "x"):
                                                         inventory.Rollup(files=1, bytes=1)},
                                                        [fx["root"]]))
    except Exception as exc:  # noqa: BLE001 - the suite must report, not crash
        res.check("suite completed", False, "%s: %s" % (type(exc).__name__, exc))
        import traceback
        res.rows.append({"ac": "TRACE", "name": "traceback", "ok": False,
                         "detail": traceback.format_exc()[-600:]})
    res.rows.append({"ac": "INFO", "name": "fixture_root", "ok": True,
                     "detail": fx["root"]})
    res.rows.append({"ac": "INFO", "name": "fixture_cleanup", "ok": True,
                     "detail": "tools/purge_fixture.py <fixture_root>"})
    return res
