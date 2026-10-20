#!/usr/bin/env python3
"""桌面分拣自主执行器（Phase 2）。

安全模型：
  - 只执行规划器判定为 action=MOVE 的条目；REVIEW / KEEP 永不执行。
  - 跨卷不做 move：复制 -> 逐文件 SHA-256 校验 -> 源文件移入回收区。
  - 全程零删除：不调用 os.remove / unlink / rmtree。
  - 目标已存在 -> 跳过，不覆盖、不合并。
  - 每次运行有锁、预算、journal、manifest；可 --undo 回滚。

用法：
  python tools/desktop_triage_apply.py --dry-run        # 只列可执行项
  python tools/desktop_triage_apply.py --auto           # 自主执行
  python tools/desktop_triage_apply.py --undo <run_id>  # 回滚一次运行
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import tomllib
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import desktop_triage as planner  # noqa: E402

DEFAULT_ROOT = planner.DEFAULT_ROOT
DEFAULT_CONFIG = planner.DEFAULT_CONFIG
STATE = os.path.join(REPO, ".state", "desktop_triage")
JOURNAL = os.path.join(STATE, "journal.ndjson")
LOCK = os.path.join(STATE, ".apply.lock")
EXCLUDE_REASONS = {"DEST_EXISTS", "DEST_IS_DIR_MERGE_NEEDED"}


class ApplyError(Exception):
    pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def append_jsonl(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_reparse(path: str) -> bool:
    try:
        return os.path.islink(path) or bool(
            getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
            & 0x400)
    except OSError:
        return False


def tree_manifest(root: str) -> list[tuple[str, int, str]]:
    """(rel, size, sha256) for every regular file; reparse anywhere aborts."""
    rows = []
    for base, dirs, files in os.walk(root, followlinks=False):
        for d in list(dirs):
            if is_reparse(os.path.join(base, d)):
                raise ApplyError("reparse point inside tree: %s" % os.path.join(base, d))
        for fn in files:
            fp = os.path.join(base, fn)
            if is_reparse(fp):
                raise ApplyError("reparse file inside tree: %s" % fp)
            st = os.stat(fp, follow_symlinks=False)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            rows.append((rel, st.st_size, sha256_file(fp)))
    rows.sort()
    return rows


def digest(rows: list[tuple[str, int, str]]) -> str:
    h = hashlib.sha256()
    for rel, size, sha in rows:
        h.update(("%s\0%d\0%s\n" % (rel, size, sha)).encode("utf-8"))
    return h.hexdigest()


def under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == \
            os.path.abspath(root)
    except ValueError:
        return False


def dest_allowed(dest: str, cfg: dict) -> bool:
    roots = [cfg["dest"]["active"], cfg["dest"]["reference"],
             cfg["dest"]["archive"], cfg["dest"]["unclassified"],
             cfg["dest"]["software"]]
    return any(under(dest, r) for r in roots)


def acquire_lock(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
            pid = int(info.get("pid", 0))
            started = float(info.get("started", 0))
            released = bool(info.get("released", False))
        except (OSError, ValueError):
            pid, started, released = 0, 0, True
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        if alive and not released and time.time() - started < 6 * 3600:
            raise ApplyError("another run is active (pid %d)" % pid)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started": time.time(),
                   "ts": now_iso(), "released": False}, f)


def release_lock(path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started": time.time(),
                       "ts": now_iso(), "released": True}, f)
    except OSError:
        pass


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    if "auto" not in cfg:
        raise ApplyError("config missing [auto] section")
    return cfg


def eligible(row: dict, cfg: dict, root: str) -> tuple[bool, str]:
    if row.get("action") != "MOVE":
        return False, "NOT_MOVE"
    if row.get("reason") in EXCLUDE_REASONS:
        return False, row["reason"]
    src, dest = row.get("src", ""), row.get("dest", "")
    if not src or not dest:
        return False, "EMPTY_PATH"
    if not under(src, root):
        return False, "SRC_OUTSIDE_ROOT"
    if not dest_allowed(dest, cfg):
        return False, "DEST_NOT_ALLOWED"
    if under(dest, root):
        return False, "DEST_INSIDE_ROOT"
    if under(dest, src):
        return False, "DEST_INSIDE_SRC"
    if not os.path.lexists(src):
        return False, "SRC_MISSING"
    if is_reparse(src):
        return False, "SRC_REPARSE"
    if row.get("confidence") == "LOW" and not cfg["auto"].get(
            "auto_low_confidence", True):
        return False, "LOW_CONFIDENCE_HELD"
    if os.path.lexists(dest):
        return False, "DEST_EXISTS"
    if row.get("kind") == "DIR" and int(row.get("files", 0)) == 0:
        return False, "EMPTY_DIR"
    return True, "OK"


def copy_verify(src: str, dest: str, run_id: str, idx: int,
                partial_root: str) -> dict:
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    staging = os.path.join(parent, ".tri-partial-%s-%03d" % (run_id, idx))
    if os.path.lexists(staging):
        raise ApplyError("staging already exists: %s" % staging)

    try:
        if os.path.isdir(src):
            src_rows = tree_manifest(src)
            shutil.copytree(src, staging, copy_function=shutil.copy2, symlinks=False)
            dst_rows = tree_manifest(staging)
        else:
            src_rows = [(os.path.basename(src), os.path.getsize(src), sha256_file(src))]
            shutil.copy2(src, staging)
            dst_rows = [(os.path.basename(src), os.path.getsize(staging),
                         sha256_file(staging))]
        if src_rows != dst_rows:
            raise ApplyError("verification mismatch: %s" % src)
        os.rename(staging, dest)
    except Exception:
        if os.path.lexists(staging):
            target = os.path.join(partial_root, "%03d" % idx, os.path.basename(dest))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.lexists(target):
                target += ".%d" % int(time.time())
            try:
                shutil.move(staging, target)
            except OSError:
                pass
        raise
    return {"files": len(src_rows), "bytes": sum(r[1] for r in src_rows),
            "tree_sha256": digest(src_rows)}


def move_to_trash(src: str, trash_root: str, run_id: str, rel: str) -> str:
    target = os.path.join(trash_root, run_id, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.lexists(target):
        raise ApplyError("trash target exists: %s" % target)
    shutil.move(src, target)
    return target


def select(rows: list[dict], cfg: dict, root: str, auto: bool) -> tuple[list, list]:
    ok, skipped = [], []
    for r in rows:
        good, why = eligible(r, cfg, root)
        if good:
            ok.append(r)
        elif r.get("action") == "MOVE":
            skipped.append({"rel": r.get("rel"), "reason": why})
    # directories first: a cluster directory must land before its loose files
    # are merged into it, otherwise the first file creates the directory and the
    # directory move then collides with its own contents.
    ok.sort(key=lambda r: (0 if r.get("kind") == "DIR" else 1,
                           float(r.get("size_mb", 0))))
    return ok, skipped


def run(root: str, cfg_path: str, auto: bool, max_items: Optional[int],
        max_mb: Optional[int], only: str) -> int:
    cfg = load_config(cfg_path)
    run_id = time.strftime("%Y%m%d_%H%M%S")
    state = os.path.abspath(cfg["auto"].get("state_dir", STATE))
    if os.path.exists(os.path.join(state, "HOLD")):
        print("HOLD present: %s" % os.path.join(state, "HOLD"))
        return 0
    run_dir = os.path.join(state, "runs", run_id)
    journal = os.path.join(state, "journal.ndjson")
    lock = os.path.join(state, ".apply.lock")

    pl = planner.build(cfg, root, run_id, do_hash=False)
    rows = pl.items
    if only:
        rows = [r for r in rows if only.lower() in (r.get("rel") or "").lower()]
    ok, skipped = select(rows, cfg, root, auto)

    limit = max_items if max_items is not None else int(cfg["auto"]["max_items"])
    budget = (max_mb * 1024 * 1024) if max_mb is not None else int(
        cfg["auto"]["max_bytes"])
    picked, held = [], []
    total = 0
    for r in ok:
        size = int(float(r.get("size_mb", 0)) * 1e6)
        if len(picked) >= limit or total + size > budget:
            held.append(r)
            continue
        picked.append(r)
        total += size

    summary = {"run_id": run_id, "root": root, "mode": "AUTO" if auto else "DRY_RUN",
               "trash_root": cfg["auto"]["trash_root"],
               "candidates": len(ok), "picked": len(picked), "held_by_budget": len(held),
               "moved": 0, "failed": 0, "results": [], "skipped": skipped}
    print("run_id=%s mode=%s candidates=%d picked=%d held=%d bytes~%.1fMB" %
          (run_id, summary["mode"], len(ok), len(picked), len(held), total / 1e6))
    for r in picked:
        print("  %-8s %8.1fMB  %s -> %s" %
              (r.get("confidence"), float(r.get("size_mb", 0)), r.get("rel"), r.get("dest")))
    if not auto:
        return 0
    if not picked:
        return 0

    os.makedirs(run_dir, exist_ok=True)
    acquire_lock(lock)
    try:
        append_jsonl(journal, {"kind": "header", "run_id": run_id, "ts": now_iso(),
                               "root": root, "mode": "AUTO", "policy": cfg_path})
        deadline = time.time() + float(cfg["auto"]["max_wall_s"])
        for i, r in enumerate(picked):
            src, dest = r["src"], r["dest"]
            rel = r.get("rel") or os.path.basename(src)
            event_id = "d_%s_%03d" % (run_id, i)
            append_jsonl(journal, {"kind": "event", "event_id": event_id, "run_id": run_id,
                                   "state": "planned", "src": src, "dst": dest,
                                   "kind": r.get("kind"), "ts": now_iso()})
            if time.time() > deadline:
                summary["results"].append({"event_id": event_id, "rel": rel,
                                           "state": "held_budget_wall"})
                break
            try:
                info = copy_verify(src, dest, run_id, i,
                                   os.path.join(cfg["auto"]["trash_root"], run_id, "_partial"))
                trash = move_to_trash(src, cfg["auto"]["trash_root"], run_id, rel)
                append_jsonl(journal, {"kind": "event", "event_id": event_id,
                                       "run_id": run_id, "state": "done",
                                       "src": src, "dst": dest, "trash": trash,
                                       "src_hash": info["tree_sha256"],
                                       "files": info["files"], "bytes": info["bytes"],
                                       "ts": now_iso()})
                summary["moved"] += 1
                summary["results"].append({"event_id": event_id, "rel": rel,
                                           "state": "done", "dst": dest,
                                           "trash": trash, **info})
            except Exception as exc:  # keep going; nothing is deleted
                append_jsonl(journal, {"kind": "event", "event_id": event_id,
                                       "run_id": run_id, "state": "failed",
                                       "src": src, "dst": dest, "error": str(exc),
                                       "ts": now_iso()})
                summary["failed"] += 1
                summary["results"].append({"event_id": event_id, "rel": rel,
                                           "state": "failed", "error": str(exc)})
    finally:
        release_lock(lock)

    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(run_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("# Desktop triage run %s\n\n" % run_id)
        f.write("- mode: %s\n- moved: %d\n- failed: %d\n- held: %d\n\n" %
                (summary["mode"], summary["moved"], summary["failed"],
                 summary["held_by_budget"]))
        for x in summary["results"]:
            f.write("- %s: %s%s\n" % (x["state"], x["rel"],
                                      (" (%s)" % x.get("error", "")) if x.get("error") else ""))
    print("moved=%d failed=%d manifest=%s" %
          (summary["moved"], summary["failed"], run_dir))
    return 0 if summary["failed"] == 0 else 1


def undo(run_id: str, state_dir: str = STATE) -> int:
    state = os.path.abspath(state_dir)
    journal = os.path.join(state, "journal.ndjson")
    manifest_path = os.path.join(state, "runs", run_id, "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        man = json.load(f)
    done = [x for x in man.get("results", []) if x.get("state") == "done"]
    if not done:
        print("nothing to undo")
        return 0
    moved = 0
    for x in reversed(done):
        dst = x.get("dst", "")
        trash = x.get("trash", "")
        if not dst or not trash or not os.path.lexists(dst):
            print("skip (dst missing): %s" % dst)
            continue
        if os.path.lexists(trash) is False:
            print("skip (trash missing): %s" % trash)
            continue
        # verify destination still matches what we wrote
        rows = tree_manifest(dst) if os.path.isdir(dst) else [
            (os.path.basename(dst), os.path.getsize(dst), sha256_file(dst))]
        if digest(rows) != x.get("tree_sha256"):
            print("skip (dst changed since run): %s" % dst)
            continue
        src = man["root"] + "\\" + x["rel"].replace("/", "\\")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        if os.path.lexists(src):
            print("skip (src occupied): %s" % src)
            continue
        # move the copy out of D:\data into its own trash first (no deletion)
        side = os.path.join(man.get("undo_trash", r"D:\data\_System\Trash\DesktopTriage"),
                            run_id, x["rel"].replace("/", "\\"))
        os.makedirs(os.path.dirname(side), exist_ok=True)
        if os.path.lexists(side):
            print("skip (undo trash exists): %s" % side)
            continue
        shutil.move(dst, side)
        try:
            shutil.move(trash, src)
        except OSError as exc:
            shutil.move(side, dst)  # put it back where it was
            print("failed (restore): %s %s" % (src, exc))
            continue
        moved += 1
        append_jsonl(journal, {"kind": "event", "run_id": run_id, "state": "undo",
                               "src": src, "dst": dst, "side_trash": side,
                               "ts": now_iso()})
    print("undo moved=%d" % moved)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Desktop triage autonomous executor")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--auto", action="store_true", help="execute (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-items", type=int)
    ap.add_argument("--budget-mb", type=int)
    ap.add_argument("--only", default="", help="filter by path substring")
    ap.add_argument("--undo", default="")
    ap.add_argument("--state", default="", help="state dir override (for undo)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if args.undo:
        return undo(args.undo, args.state or STATE)
    if not os.path.isdir(root):
        print("root not found: %s" % root, file=sys.stderr)
        return 2
    try:
        return run(root, args.config, args.auto and not args.dry_run,
                   args.max_items, args.budget_mb, args.only)
    except ApplyError as exc:
        print("apply error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
