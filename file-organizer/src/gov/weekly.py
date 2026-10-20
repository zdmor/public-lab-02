"""P1-2 decision weekly: six-question read-only digest.

This is a pure aggregation layer. It reads the newest snapshot (directory
growth / accumulation) and the newest candidates file (safe-to-clean / needs
human review) and answers the six standing questions Ding Zhai set for the
weekly review. No scan, no write, no delete primitive (PRD I1).

Six questions:
  1. largest growing directories (top by bytes, from snapshot)
  2. what is safely cleanable now      (AUTO_SAFE candidates)
  3. what needs a human decision       (REVIEW_REQUIRED candidates)
  4. top-N actionable by size          (cleanable, sorted desc)
  5. estimated space recovered         (sum of cleanable bytes)
  6. anomalous accumulation            (snapshot dirs with stale mtime + size)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import core, inventory


@dataclass
class WeeklyReport:
    generated: str = ""
    snapshot: str = ""
    candidates_file: str = ""
    q1_growing: list = field(default_factory=list)   # [{dir, bytes, files}]
    q2_cleanable: dict = field(default_factory=dict)  # {count, bytes, top:[...]}
    q3_review: dict = field(default_factory=dict)     # {count, bytes, top:[...]}
    q4_topn: list = field(default_factory=list)       # [{path, size}]
    q5_estimated_free: int = 0
    q6_anomaly: list = field(default_factory=list)    # [{dir, bytes, max_mtime_days}]
    q7_buckets: dict = field(default_factory=dict)    # {clean:[...], review:[...]}
    coverage_warning: str = ""

    def as_dict(self) -> dict:
        return {
            "generated": self.generated,
            "snapshot": self.snapshot,
            "candidates_file": self.candidates_file,
            "q1_largest_growing": self.q1_growing,
            "q2_safely_cleanable": self.q2_cleanable,
            "q3_needs_human": self.q3_review,
            "q4_top_actionable": self.q4_topn,
            "q5_estimated_free": core.human_gb(self.q5_estimated_free),
            "q6_anomalous_accumulation": self.q6_anomaly,
            "q7_decision_buckets": self.q7_buckets,
            "coverage_warning": self.coverage_warning,
        }


def _latest_candidates(state_dir: str) -> str:
    d = os.path.join(core.norm(state_dir), "candidates")
    if not os.path.isdir(d):
        return ""
    names = [n for n in os.listdir(d) if n.endswith(".jsonl")]
    if not names:
        return ""
    # By mtime, not by name: snapshots used to be named per day, now they carry
    # a run timestamp, and a name sort mixes the two schemes. The newest write
    # is the newest verdict, whatever it is called.
    return os.path.join(d, max(names, key=lambda n: os.path.getmtime(os.path.join(d, n))))


def _is_cleanable(row: dict) -> bool:
    return row.get("risk_class") == core.RISK_CLASSES[0]


def _needs_human(row: dict) -> bool:
    return row.get("risk_class") == "REVIEW_REQUIRED"


def _buckets(rows: list, top: int) -> list:
    """Collapse a per-file decision surface into per-directory buckets.

    187k REVIEW_REQUIRED rows cannot be approved one at a time — not by a human
    on a Sunday. But they are not 187k decisions either: they are a few dozen
    "this whole directory is junk" calls. One bucket = one plan = one
    --approve, which is the only granularity the executor already supports.
    """
    agg: dict = {}
    for r in rows:
        path = r.get("path") or ""
        d = os.path.dirname(path) or path
        b = agg.setdefault(d, {"dir": d, "files": 0, "bytes": 0})
        b["files"] += 1
        b["bytes"] += int(r.get("size") or 0)
    return sorted(agg.values(), key=lambda x: -x["bytes"])[:top]


def build(pol, *, top: int = 20) -> WeeklyReport:
    """Aggregate the six answers from the newest snapshot + candidates."""
    rep = WeeklyReport(generated=core.now_iso())

    # --- snapshot side (q1 growth, q6 anomaly) ---
    snap = inventory.latest_snapshot(pol.state_dir, pol.hostname)
    snap_volumes: set = set()
    if snap:
        rep.snapshot = snap
        loaded = inventory.load_snapshot(snap)
        dirs = loaded["dirs"]
        roots = (loaded["header"].get("stats") or {}).get("roots") or []
        root_lows = {core.low(r) for r in roots}
        snap_volumes = {core.drive_of(r).lower() for r in roots if r}

        # q1: largest directories by subtree bytes (folded snapshot rows)
        rows = []
        for path, r in dirs.items():
            pl = core.low(path)
            if any(core.prefixed(pl, rl) for rl in root_lows):
                rows.append({"dir": path,
                             "bytes": int(r.get("bytes") or 0),
                             "files": int(r.get("files") or 0)})
        rows.sort(key=lambda x: -x["bytes"])
        rep.q1_growing = rows[:top]

        # q6: accumulation = dirs with stale max_mtime but still large
        now = core.now_ts()
        for path, r in dirs.items():
            mt = float(r.get("max_mtime") or 0)
            if mt <= 0:
                continue
            age_days = (now - mt) / 86400.0
            nbytes = int(r.get("bytes") or 0)
            if age_days > 30 and nbytes > 1 << 30:  # >30d stale, >1 GiB
                rep.q6_anomaly.append({"dir": path, "bytes": nbytes,
                                       "max_mtime_days": round(age_days, 1)})
        rep.q6_anomaly.sort(key=lambda x: -x["bytes"])

    # --- candidates side (q2/q3/q4/q5) ---
    cand_file = _latest_candidates(pol.state_dir)
    cand_volumes: set = set()
    if cand_file:
        rep.candidates_file = cand_file
        clean_map, review_map = {}, {}
        for row in core.iter_ndjson(cand_file):
            p = row.get("path")
            if not p:
                continue
            if p.startswith("\\\\"):
                # device pseudo-file (\\.\NUL and friends) — a scan artefact,
                # never user data and never a volume of its own
                continue
            cand_volumes.add(core.drive_of(p).lower())
            if _is_cleanable(row):
                # a path scanned under several roots appears once per root; keep
                # the largest record so totals are not inflated by duplicates
                cur = clean_map.get(p)
                if cur is None or int(row.get("size") or 0) > int(cur.get("size") or 0):
                    clean_map[p] = row
            elif _needs_human(row):
                cur = review_map.get(p)
                if cur is None or int(row.get("size") or 0) > int(cur.get("size") or 0):
                    review_map[p] = row
        clean = list(clean_map.values())
        review = list(review_map.values())

        rep.q2_cleanable = {
            "count": len(clean),
            "bytes": sum(int(r.get("size") or 0) for r in clean),
            "top": sorted(
                ({"path": r["path"], "size": int(r.get("size") or 0),
                  "rule": ",".join(r.get("rule_ids") or [])}
                 for r in clean), key=lambda x: -x["size"])[:top],
        }
        rep.q3_review = {
            "count": len(review),
            "bytes": sum(int(r.get("size") or 0) for r in review),
            "top": sorted(
                ({"path": r["path"], "size": int(r.get("size") or 0)}
                 for r in review), key=lambda x: -x["size"])[:top],
        }
        rep.q4_topn = sorted(
            ({"path": r["path"], "size": int(r.get("size") or 0)}
             for r in clean), key=lambda x: -x["size"])[:top]
        rep.q5_estimated_free = sum(int(r.get("size") or 0) for r in clean)
        rep.q7_buckets = {"clean": _buckets(clean, top),
                          "review": _buckets(review, top)}

    # The digest silently mixes two independent artefacts: growth/accumulation
    # come from the newest snapshot, the actionable lists from the newest
    # candidates file. If those two disagree on which volumes they cover, the
    # reader would be comparing C: growth against D: candidates without being
    # told. Say so instead of blending them quietly.
    if snap_volumes and cand_volumes and snap_volumes != cand_volumes:
        only_snap = ",".join(sorted(snap_volumes - cand_volumes)) or "-"
        only_cand = ",".join(sorted(cand_volumes - snap_volumes)) or "-"
        rep.coverage_warning = (
            "snapshot covers {%s} but candidates covers {%s} (snapshot-only=%s, "
            "candidates-only=%s) — Q1/Q6 and Q2-Q7 describe different volumes; "
            "re-run `inventory` and `candidates --json` over the same roots"
            % (",".join(sorted(snap_volumes)), ",".join(sorted(cand_volumes)),
               only_snap, only_cand))

    return rep


def render(rep: WeeklyReport) -> str:
    lines = ["# 决策周报 %s" % rep.generated]
    lines.append("snapshot: %s" % (rep.snapshot or "(none)"))
    lines.append("candidates: %s" % (rep.candidates_file or "(none)"))
    if rep.coverage_warning:
        lines.append("")
        lines.append("!! 覆盖不一致: %s" % rep.coverage_warning)
    lines.append("")

    lines.append("## Q1 增长最大的目录 (top bytes)")
    for r in rep.q1_growing:
        lines.append("  %10s %9d  %s" % (core.human_gb(r["bytes"]), r["files"], r["dir"]))
    lines.append("")

    lines.append("## Q2 可安全清理 (AUTO_SAFE)")
    q2 = rep.q2_cleanable
    lines.append("  count=%d  bytes=%s" % (q2.get("count", 0),
                                            core.human_gb(q2.get("bytes", 0))))
    for r in q2.get("top", [])[:10]:
        lines.append("  %10s  %s  [%s]" % (core.human_gb(r["size"]), r["path"], r["rule"]))
    lines.append("")

    lines.append("## Q3 需要人工决策 (REVIEW_REQUIRED)")
    q3 = rep.q3_review
    lines.append("  count=%d  bytes=%s" % (q3.get("count", 0),
                                            core.human_gb(q3.get("bytes", 0))))
    for r in q3.get("top", [])[:10]:
        lines.append("  %10s  %s" % (core.human_gb(r["size"]), r["path"]))
    lines.append("")

    lines.append("## Q4 Top-N 可操作 (按大小)")
    for r in rep.q4_topn[:10]:
        lines.append("  %10s  %s" % (core.human_gb(r["size"]), r["path"]))
    lines.append("")

    lines.append("## Q5 预计释放空间")
    lines.append("  %s" % core.human_gb(rep.q5_estimated_free))
    lines.append("")

    lines.append("## Q6 异常堆积 (>30d 未更新 且 >1GiB)")
    for r in rep.q6_anomaly:
        lines.append("  %10s  %s  (%.0f 天未更新)" %
                     (core.human_gb(r["bytes"]), r["dir"], r["max_mtime_days"]))
    if not rep.q6_anomaly:
        lines.append("  (无)")
    lines.append("")

    # P2: the decision surface a human can actually work through.
    q7 = rep.q7_buckets or {}
    clean_b = q7.get("clean") or []
    review_b = q7.get("review") or []
    lines.append("## Q7 决策桶 (按目录聚合 — 一个桶 = 一次批准)")
    if clean_b:
        lines.append("  AUTO_SAFE 桶 (gov plan --root <桶> --kind MOVE 即可执行):")
        for b in clean_b:
            lines.append("    %10s  %7d 文件  %s" %
                         (core.human_gb(b["bytes"]), b["files"], b["dir"]))
    else:
        lines.append("  AUTO_SAFE 桶: (无)")
    lines.append("")
    if review_b:
        lines.append("  REVIEW_REQUIRED 桶 (需人工判断，不能自动执行):")
        for b in review_b:
            lines.append("    %10s  %7d 文件  %s" %
                         (core.human_gb(b["bytes"]), b["files"], b["dir"]))
        lines.append("")
        lines.append("  提示: REVIEW 桶不会自动清理。要释放其中某桶的空间，需要先把该目录")
        lines.append("        写成一条明确规则 (policy/common.toml) 并走人工批准，或把它")
        lines.append("        整体判为废件后走 gov release 的永久删除流程。")
    else:
        lines.append("  REVIEW_REQUIRED 桶: (无)")
    return "\n".join(lines) + "\n"
