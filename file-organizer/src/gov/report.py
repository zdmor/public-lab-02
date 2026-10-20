"""Human-readable reports and the read-only proof manifest (PRD FR6 / AC4)."""
from __future__ import annotations

import os
import time
from typing import Iterable, Optional, Sequence

from . import core, inventory
from .config import Policy


def _fmt_bytes(n: float) -> str:
    return "%.3f GB" % (float(n) / 1024.0 ** 3)


class ManifestError(Exception):
    pass


def manifest(root: str, rules, *, max_files: int = 2_500_000,
             exclude: Sequence[str] = ()) -> tuple[list[tuple], dict]:
    """(path, size, mtime_us, attrs) for every visible file under `root`.

    Used to prove a read-only run changed nothing: run before, run after,
    compare. A walk that is empty or stops on a budget raises instead of
    handing back a proof that could not have failed (AC4).

    `exclude` names prefixes a live third-party app writes to (an app log dir,
    a browser cache). They are never silently dropped: every suppressed path
    is counted and sampled in the returned meta, so the proof states what it
    did not compare.
    """
    if not os.path.isdir(core.norm(root)):
        raise ManifestError("manifest root missing: %s" % root)
    excl = tuple(core.low(core.norm(x)) for x in exclude)
    rows: list[tuple] = []
    dropped: list[str] = []
    budget = core.Budget(max_wall_s=1800, max_files=max_files, max_bytes=10 ** 18)
    stats = inventory.ScanStats()

    def emit(rec: core.Record) -> None:
        # the walk already lstat'ed this file; re-stat'ing it here doubles the
        # syscalls on a 700k-file root for no extra evidence
        if excl and core.under_prefix(rec.path, excl):
            dropped.append(rec.path)
            return
        rows.append((core.low(rec.path), rec.size, int(rec.mtime * 1_000_000),
                     int(rec.attrs_hex, 16)))

    inventory.walk(root, rules=rules, budget=budget, emit=emit, stats=stats)
    if stats.budget_hit:
        raise ManifestError("manifest truncated by budget=%s" % stats.budget_hit)
    if not rows:
        raise ManifestError("manifest empty for %s" % root)
    rows.sort()
    dropped.sort()
    return rows, {"root": root, "files": len(rows), "bytes": sum(r[1] for r in rows),
                  "excluded_prefixes": list(excl), "excluded_files": len(dropped),
                  "excluded_sample": dropped[:5],
                  "wall_s": round(stats.wall_s, 3),
                  "files_per_s": round(stats.files / max(stats.wall_s, 0.001), 1)}


def manifest_diff(before: Sequence[tuple], after: Sequence[tuple]) -> dict:
    """Decompose a proof comparison: added / removed / content-changed.

    Anything this tool can do to a scanned tree shows up as a path appearing
    or disappearing (a MOVE leaves the source gone). A row that is in both
    path sets with a different size or mtime is a rewrite, reported apart so it
    can never be waved through as 'same file'.
    """
    b, a = set(before), set(after)
    same = {x[0] for x in (b & a)}
    bp, ap = {x[0] for x in b}, {x[0] for x in a}
    changed = sorted(((bp & ap) - same))
    return {"identical": b == a,
            "paths_added": len(ap - bp), "paths_removed": len(bp - ap),
            "paths_changed": len(changed),
            "count_before": len(before), "count_after": len(after),
            "bytes_before": sum(x[1] for x in before),
            "bytes_after": sum(x[1] for x in after),
            "changed_sample": changed[:5],
            "samples": sorted(list(b - a)[:5]) + sorted(list(a - b)[:5])}


def health(pol: Policy, roots: Sequence[str], volumes: Sequence[dict],
           dirs: Optional[dict] = None, stats: Optional[inventory.ScanStats] = None,
           journal_summary: Optional[dict] = None,
           toplevel: Optional[dict] = None) -> str:
    lines = ["# health %s %s" % (pol.hostname, core.now_iso()),
             "policy: %s v%s  sources=%d" % (pol.policy_id, pol.policy_version,
                                              len(pol.source_files))]
    for v in volumes:
        if "error" in v:
            lines.append("volume %-4s READ FAILED (%s)" % (v["root"], v["error"]))
            continue
        lines.append("volume %-4s total=%s free=%s (%.1f%% free)"
                     % (v["root"], _fmt_bytes(v["total"]), _fmt_bytes(v["free"]),
                        v["pct_free"]))
    warn = [v for v in volumes if "pct_free" in v and v["pct_free"] < pol.scan.get(
        "low_space_pct", 10)]
    if warn:
        lines.append("!! low free space on: %s" % ", ".join(v["root"] for v in warn))
    for root in roots:
        entries = inventory.top_shallow(root, rules=pol)
        lines.append("root %s : %d top-level entries" % (root, len(entries)))
    if toplevel:
        for root, change in toplevel.items():
            if change.get("baseline"):
                lines.append("toplevel %s: no comparison yet (%s)"
                             % (root, change["baseline"]))
                continue
            if change.get("new") or change.get("gone"):
                lines.append("toplevel change %s: new=%s gone=%s"
                             % (root, change["new"], change["gone"]))
    if stats:
        lines.append("last scan: files=%d bytes=%s wall=%.1fs denied=%d reparse=%d "
                     "budget_hit=%s" % (stats.files, _fmt_bytes(stats.bytes_total),
                                         stats.wall_s, len(stats.denied),
                                         len(stats.reparse), stats.budget_hit))
    if journal_summary:
        lines.append("journal: events=%d by_state=%s unfinished=%s"
                     % (journal_summary.get("events", 0),
                        journal_summary.get("by_state", {}),
                        journal_summary.get("unfinished", [])))
    return "\n".join(lines) + "\n"


def render_plan(actions: Iterable, *, limit: int = 50) -> str:
    acts = list(actions)
    blocked = [a for a in acts if a.reject]
    go = [a for a in acts if not a.reject]
    lines = ["# plan %s : %d actionable, %d blocked, %d bytes (%s)"
             % (acts[0].plan_id if acts else "-", len(go), len(blocked),
                sum(a.size for a in go), _fmt_bytes(sum(a.size for a in go)))]
    for a in go[:limit]:
        lines.append("MOVE  %s -> %s  [%s %s]" % (a.src, a.dst, a.risk_class,
                                                   ",".join(a.rule_ids)))
    for a in blocked[:limit]:
        lines.append("BLOCK %s : %s" % (a.src, a.reject))
    if len(acts) > limit * 2:
        lines.append("... %d more lines suppressed" % (len(acts) - limit * 2))
    return "\n".join(lines) + "\n"


def render_candidates(cands: Sequence, *, limit: int = 25) -> str:
    from . import candidates as cand_mod
    summary = cand_mod.summarize(cands)
    lines = ["# candidates: %d records" % len(cands)]
    for risk in core.RISK_CLASSES:
        b = summary.get(risk)
        if not b:
            continue
        lines.append("%s: %d files, %s" % (risk, b["count"], _fmt_bytes(b["bytes"])))
        for rid, v in sorted(b["rules"].items(), key=lambda kv: -kv[1]["bytes"])[:8]:
            lines.append("    %-34s %6d files  %10s" % (rid, v["count"],
                                                         _fmt_bytes(v["bytes"])))
    lines.append("")
    for c in sorted(cands, key=lambda x: -x.size)[:limit]:
        lines.append("%-14s %10s  %s" % (c.risk_class, _fmt_bytes(c.size), c.path))
    return "\n".join(lines) + "\n"


def render_rollup(dirs: dict, root: str, *, top: int = 20) -> str:
    rows = []
    root_l = core.low(root)
    for path, r in dirs.items():
        # core.prefixed, not `root + "\"`: a volume root already ends in a
        # separator, and "d:\\" + "\" matches no real path, which left the
        # report with a single row for `--root D:\`
        if core.prefixed(core.low(path), root_l):
            rows.append((path, getattr(r, "files", 0), getattr(r, "bytes", 0)))
    rows.sort(key=lambda x: -x[2])
    lines = ["# top directories under %s" % root]
    for path, files, nbytes in rows[:top]:
        lines.append("%10s %9d  %s" % (_fmt_bytes(nbytes), files, path))
    return "\n".join(lines) + "\n"


def render_journal(events: Sequence[dict]) -> str:
    lines = ["# journal report (%d records)" % len(events)]
    for ev in events[-100:]:
        lines.append("%-8s %-7s %s -> %s  [%s]" % (ev.get("state"), ev.get("action"),
                                                    ev.get("src"), ev.get("dst"),
                                                    ev.get("event_id")))
    return "\n".join(lines) + "\n"


def save(state_dir: str, name: str, text: str) -> str:
    path = os.path.join(core.norm(state_dir), "report",
                        "%s-%s.txt" % (time.strftime("%Y%m%d_%H%M%S"), name))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def save_jsonl(path: str, rows: Iterable[dict]) -> str:
    # one open handle: per-row append_ndjson also mkdir's each time, which cost
    # ~100 s on a 185k-line candidate file
    core.append_ndjson_many(path, rows)
    return path
