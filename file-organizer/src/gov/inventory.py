"""Layer A: read-only inventory. Writes only into the state dir (PRD I2)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from . import core

BUDGET_CHECK_EVERY = core.BUDGET_CHECK_EVERY


@dataclass
class ScanStats:
    roots: list = field(default_factory=list)
    dirs_visited: int = 0
    files: int = 0
    dirs: int = 0
    bytes_total: int = 0
    denied: list = field(default_factory=list)
    reparse: list = field(default_factory=list)
    broken_links: list = field(default_factory=list)
    prunes: dict = field(default_factory=dict)
    budget_hit: Optional[str] = None
    wall_s: float = 0.0
    scan_id: str = ""
    machine: str = field(default_factory=core.machine_name)

    def note_prune(self, reason: str) -> None:
        self.prunes[reason] = self.prunes.get(reason, 0) + 1

    def as_dict(self) -> dict:
        return {
            "scan_id": self.scan_id, "machine": self.machine,
            "roots": list(self.roots), "dirs_visited": self.dirs_visited,
            "files": self.files, "dirs": self.dirs, "bytes": self.bytes_total,
            "denied_count": len(self.denied), "denied": self.denied[:200],
            "reparse_count": len(self.reparse), "reparse": self.reparse[:200],
            "broken_links": self.broken_links[:200],
            "prunes": dict(self.prunes), "budget_hit": self.budget_hit,
            "wall_s": round(self.wall_s, 3),
            "files_per_s": round(self.files / max(self.wall_s, 0.001), 1),
        }


def _reparse_tag(entry) -> str:
    try:
        if entry.is_junction():
            return "junction"
    except (AttributeError, OSError):
        pass
    try:
        if entry.is_symlink():
            return "symlink"
    except OSError:
        pass
    return "reparse"


def _is_dir(entry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def walk(root: str, *, rules: core.ScanRules, budget: core.Budget,
         zone_role: str = "", emit: Optional[Callable[[core.Record], None]] = None,
         stats: Optional[ScanStats] = None,
         scan_id: str = "", on_dir: Optional[Callable[[str, int, int, float], None]] = None,
         max_depth: int = 0
         ) -> ScanStats:
    """Iterative, never-descending scan of one root.

    `on_dir(dirpath, nfiles, nbytes, max_mtime)` is called per visited
    directory so callers can aggregate without holding every record.
    `max_depth` (>0) stops descent at that many levels and is recorded, never
    silent (used by the bounded reparse enumeration in PRD FR3).
    """
    stats = stats or ScanStats()
    scan_id = scan_id or new_scan_id()
    root = core.norm(root)
    if root not in stats.roots:
        stats.roots.append(root)
    ts = core.now_iso()
    stack = [(root, 0)]
    since_check = 0
    while stack:
        d, depth = stack.pop()
        stats.dirs_visited += 1
        own_files = own_bytes = 0
        own_max_mtime = 0.0
        try:
            it = os.scandir(d)
        except OSError as exc:
            stats.denied.append([d, type(exc).__name__, str(exc)[:120]])
            continue
        with it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    stats.denied.append([entry.path, type(exc).__name__,
                                         str(exc)[:120]])
                    continue
                isdir = _is_dir(entry)
                attrs = int(getattr(st, "st_file_attributes", 0))
                if isdir:
                    reason = core.prune_reason(entry.name, entry.path, attrs, rules)
                    if reason:
                        stats.note_prune(reason)
                        if reason == "reparse":
                            tgt = core.read_reparse_target(entry.path)
                            tag = _reparse_tag(entry)
                            stats.reparse.append([entry.path, tag, tgt,
                                                 "missing" if tgt and not os.path.lexists(tgt) else ""])
                            if emit:
                                emit(core.Record.from_entry(
                                    scan_id=scan_id, machine=stats.machine, ts=ts,
                                    abspath=entry.path, root=root, zone_role=zone_role,
                                    st=st, name=entry.name, is_dir=True,
                                    reparse_tag=tag, target=tgt))
                        continue
                    stats.dirs += 1
                    if max_depth and depth + 1 > max_depth:
                        stats.note_prune("depth_cap")
                        continue
                    stack.append((entry.path, depth + 1))
                    continue
                size = int(getattr(st, "st_size", 0) or 0)
                mtime = float(getattr(st, "st_mtime", 0) or 0)
                budget.add(size)
                stats.files += 1
                own_files += 1
                own_bytes += size
                if mtime > own_max_mtime:
                    own_max_mtime = mtime
                rec = core.Record.from_entry(
                    scan_id=scan_id, machine=stats.machine, ts=ts,
                    abspath=entry.path, root=root, zone_role=zone_role,
                    st=st, name=entry.name, is_dir=False)
                if rec.is_reparse:
                    rec.reparse_tag = _reparse_tag(entry)
                    rec.target = core.read_reparse_target(entry.path)
                    stats.reparse.append([rec.path, rec.reparse_tag, rec.target,
                                          "missing" if rec.target and
                                          not os.path.lexists(rec.target) else ""])
                if emit:
                    emit(rec)
                since_check += 1
                if since_check >= BUDGET_CHECK_EVERY:
                    since_check = 0
                    kind = budget.hit()
                    if kind:
                        stats.budget_hit = kind
                        budget.hits.append(kind)
                        break
        if not stats.budget_hit:
            # cheap once-per-directory check: a small tree must still be able to
            # report a budget hit (PRD FR2), which 512-file granularity misses
            kind = budget.hit()
            if kind:
                stats.budget_hit = kind
                budget.hits.append(kind)
        if on_dir:
            on_dir(d, own_files, own_bytes, own_max_mtime)
        if stats.budget_hit:
            break
    stats.bytes_total = budget.bytes_total
    stats.wall_s = budget.wall_s
    stats.scan_id = scan_id
    return stats


def new_scan_id() -> str:
    return "s_%s_%s" % (time.strftime("%Y%m%d_%H%M%S"), os.getpid())


@dataclass
class Rollup:
    """Per-directory totals, aggregated to ancestors after the walk."""

    own_files: int = 0
    own_bytes: int = 0
    own_max_mtime: float = 0.0
    files: int = 0
    bytes: int = 0
    children: int = 0
    denied: int = 0
    reparse: int = 0

    def as_dict(self, path: str) -> dict:
        return {"dir": path, "files": self.files, "bytes": self.bytes,
                "own_files": self.own_files, "own_bytes": self.own_bytes,
                "children": self.children,
                "max_mtime": self.own_max_mtime}


def fold_rollups(dirs: dict) -> dict:
    """Fold subtree totals upward. Separate so a caller can aggregate during
    the walk instead of paying for a second full scan."""
    for path, r in dirs.items():
        r.files = r.own_files
        r.bytes = r.own_bytes
    for path in sorted(dirs, key=lambda p: -p.count(os.sep)):
        parent = core.norm(os.path.dirname(path))
        if parent in dirs and parent != path:
            dirs[parent].files += dirs[path].files
            dirs[parent].bytes += dirs[path].bytes
            dirs[parent].children += 1
    return dirs


def aggregate() -> tuple[dict, Callable]:
    """Return ({dir: Rollup}, on_dir) so one walk can feed the rollup (FR12)."""
    dirs: dict[str, Rollup] = {}

    def on_dir(path, nfiles, nbytes, max_mtime):
        r = dirs.setdefault(core.norm(path), Rollup())
        r.own_files += nfiles
        r.own_bytes += nbytes
        r.own_max_mtime = max(r.own_max_mtime, max_mtime)
    return dirs, on_dir


def rollup(roots: Sequence[str], *, rules: core.ScanRules,
           budget: Optional[core.Budget] = None,
           zone_role: str = "") -> tuple[dict, ScanStats]:
    """Scan and return {dir: Rollup} with subtree totals folded upward."""
    budget = budget or core.Budget()
    dirs, on_dir = aggregate()
    stats = ScanStats()
    for root in roots:
        walk(root, rules=rules, budget=budget, stats=stats, zone_role=zone_role,
             on_dir=on_dir)
    return fold_rollups(dirs), stats


def top_shallow(root: str, *, rules: core.ScanRules) -> list[dict]:
    """Non-recursive listing used by `health` (measured 9.9 ms per root)."""
    out = []
    try:
        with os.scandir(core.norm(root)) as it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    out.append({"name": entry.name, "path": entry.path,
                                "error": "stat_denied"})
                    continue
                attrs = int(getattr(st, "st_file_attributes", 0))
                out.append({
                    "name": entry.name, "path": entry.path,
                    "is_dir": _is_dir(entry), "size": int(st.st_size or 0),
                    "mtime": float(st.st_mtime or 0),
                    "is_reparse": bool(attrs & core.FILE_ATTRIBUTE_REPARSE_POINT),
                    "pruned": bool(_is_dir(entry)) and
                              core.prune_reason(entry.name, entry.path, attrs,
                                                rules) is not None,
                })
    except OSError as exc:
        out.append({"path": root, "error": type(exc).__name__})
    out.sort(key=lambda d: (-d.get("size", 0), d.get("name", "")))
    return out


def volume_free(root: str) -> dict:
    try:
        du = shutil_disk_usage(core.norm(root))
    except OSError as exc:
        return {"root": root, "error": type(exc).__name__}
    return {"root": core.norm(root), "total": du.total, "used": du.used,
            "free": du.free, "pct_free": round(100.0 * du.free / max(du.total, 1), 1)}


def shutil_disk_usage(path: str):
    from shutil import disk_usage
    return disk_usage(path)


def toplevel_dirs(dirs: Iterable[str], root: str) -> set:
    """Immediate children of `root` as recorded in `dirs`.

    The root itself is never a top-level entry of its own listing; dropping it
    here is what stops the health report from announcing a phantom `gone`
    directory with an empty name.
    """
    rl = core.low(core.norm(root))
    out = set()
    for d in dirs:
        nd = core.norm(d)
        if core.low(nd) == rl:
            continue
        if core.low(core.norm(os.path.dirname(nd))) == rl:
            out.add(nd)
    return out


def snapshot_header(path: str) -> dict:
    """First line of a snapshot: the only part `health` needs (AC5, <= 1 s)."""
    for row in core.iter_ndjson(path):
        if row.get("kind") == "header":
            return row
    return {}


def save_snapshot(state_dir: str, scan_id: str, dirs: dict,
                  stats: ScanStats) -> str:
    """One file per scan: never rewritten, so the previous scan stays diffable."""
    path = os.path.join(core.norm(state_dir), "snapshot",
                        "%s-%s-%s.ndjson" % (stats.machine, time.strftime("%Y%m%d"),
                                             scan_id))
    top = {r: sorted(os.path.basename(d) for d in toplevel_dirs(dirs, r))
           for r in stats.roots}
    core.append_ndjson(path, {"kind": "header", "scan_id": scan_id,
                              "machine": stats.machine, "ts": core.now_iso(),
                              "toplevel": top,
                              "stats": stats.as_dict()})
    core.append_ndjson_many(path, (
        dict(kind="dir", scan_id=scan_id, dir=d, files=r.files, bytes=r.bytes,
             own_files=r.own_files, own_bytes=r.own_bytes, children=r.children,
             max_mtime=r.own_max_mtime)
        for d, r in sorted(dirs.items())))
    return path


def load_snapshot(path: str) -> dict:
    header = None
    dirs = {}
    for row in core.iter_ndjson(path):
        if row.get("kind") == "header":
            header = row
        elif row.get("kind") == "dir":
            dirs[core.norm(row["dir"])] = row
    return {"header": header, "dirs": dirs, "path": path}


def latest_snapshot(state_dir: str, machine: Optional[str] = None,
                    roots: Optional[Sequence[str]] = None) -> Optional[str]:
    """Newest snapshot; with `roots`, the newest one covering every one of them.

    A snapshot of a different volume is not a baseline, it is noise: a C:\\ scan
    diffed against the previous D:\\ snapshot measured added=53638 removed=29469
    on 2026-09-22, which is the two volumes, not a change.
    """
    snap_dir = os.path.join(core.norm(state_dir), "snapshot")
    if not os.path.isdir(snap_dir):
        return None
    host = machine or core.machine_name()
    names = sorted((n for n in os.listdir(snap_dir)
                    if n.startswith(host + "-") and n.endswith(".ndjson")),
                   reverse=True)
    if not roots:
        return os.path.join(snap_dir, names[0]) if names else None
    want = {core.low(r) for r in roots}
    for n in names:
        path = os.path.join(snap_dir, n)
        have = {core.low(x) for x in
                (snapshot_header(path).get("stats") or {}).get("roots") or []}
        if want <= have:
            return path
    return None


def _num(row, key: str) -> int:
    if isinstance(row, dict):
        return int(row.get(key) or 0)
    return int(getattr(row, key, 0) or 0)


def diff_snapshot(prev: dict, cur_dirs: dict, roots: Sequence[str]) -> dict:
    """Compare two dir rollups: added / removed / grew / shrank per root."""
    res = {"added": [], "removed": [], "grew": [], "shrank": [], "toplevel": {}}
    prev_dirs = prev.get("dirs", {}) if isinstance(prev, dict) else {}
    prev_keys = set(prev_dirs)
    cur_keys = set(cur_dirs)
    for k in sorted(cur_keys - prev_keys):
        res["added"].append({"dir": k, "files": _num(cur_dirs[k], "files"),
                             "bytes": _num(cur_dirs[k], "bytes")})
    for k in sorted(prev_keys - cur_keys):
        res["removed"].append({"dir": k})
    for k in sorted(prev_keys & cur_keys):
        pb = _num(cur_dirs[k], "bytes")
        qb = _num(prev_dirs[k], "bytes")
        if pb > qb:
            res["grew"].append({"dir": k, "delta_bytes": pb - qb})
        elif pb < qb:
            res["shrank"].append({"dir": k, "delta_bytes": qb - pb})
    res["grew"].sort(key=lambda d: -d["delta_bytes"])
    for root in roots:
        top_now = {os.path.basename(d) for d in toplevel_dirs(cur_keys, root)}
        top_prev = {os.path.basename(d) for d in toplevel_dirs(prev_dirs, root)}
        res["toplevel"][core.norm(root)] = {
            "new": sorted(top_now - top_prev), "gone": sorted(top_prev - top_now),
        }
    return res
