"""Monthly Review: retention candidates + missing-copy + Trash expiry.

Issue #22 / NODE_STORAGE_LIFECYCLE_V1 "Cadence" + v3.1 section 4 (Monthly
Review). Read-only: it proposes ARCHIVE_CANDIDATE and reports; the human (or a
later CONFIRM gate) owns every retirement decision. Time only generates
candidates, never archives (v3.1 invariant #3).

Five outputs:
  m1_retention_candidates  P1/P2 batches past their retention floor (NOTIFY)
  m2_missing_second_copy   archived P0/P1 with no N2 mirror / Drive pointer
  m3_missing_drive_p0      P0-class batches with no Google Drive pointer
  m4_trash_expiry          Trash entries past the >=30d window (CONFIRM only)
  m5_growth_effectiveness  intake/archive/trash byte deltas this period

This module reads the external-archive intake plan (gov/external_archive) and
the existing release/journal state. It never moves, copies or deletes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import core
from . import external_archive as ea


@dataclass
class MonthlyReport:
    generated: str = ""
    m1_retention_candidates: list = field(default_factory=list)
    m2_missing_second_copy: list = field(default_factory=list)
    m3_missing_drive_p0: list = field(default_factory=list)
    m4_trash_expiry: list = field(default_factory=list)
    m5_growth: dict = field(default_factory=dict)
    intake_summary: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "generated": self.generated,
            "m1_retention_candidates": self.m1_retention_candidates,
            "m2_missing_second_copy": self.m2_missing_second_copy,
            "m3_missing_drive_p0": self.m3_missing_drive_p0,
            "m4_trash_expiry": self.m4_trash_expiry,
            "m5_growth_effectiveness": self.m5_growth,
            "intake_summary": self.intake_summary,
        }


def _days_old(iso: str) -> float:
    """Rough age of an ISO date relative to today (date-only precision)."""
    import datetime as _dt
    try:
        d = _dt.date.fromisoformat(iso)
    except ValueError:
        return -1.0
    return float((_dt.date.today() - d).days)


def _retention_floor_reached(plan: dict, pol) -> bool:
    """True when a P1/P2 batch's retention_review_after date is in the past."""
    cid = plan.get("lifecycle_class")
    if cid not in ("P1", "P2"):
        return False
    review = plan.get("retention_review_after") or ""
    if review in ("", "candidate_pending_completion", "indefinite"):
        return False
    age = _days_old(review)
    return age <= 0  # review date reached or passed


def build(pol) -> MonthlyReport:
    rep = MonthlyReport(generated=core.now_iso())

    intake = ea.plan_intake(pol)
    rep.intake_summary = {
        "batches": len(intake["batches"]),
        "plans": len(intake["plans"]),
        "migration_authorized": intake["migration_authorized"],
    }

    archived_batches = []
    for plan in intake["plans"]:
        cid = plan.get("lifecycle_class")
        completed = plan.get("completed")

        # m1: P1/P2 past retention floor -> candidate (NOTIFY, not auto)
        if cid in ("P1", "P2") and _retention_floor_reached(plan, pol):
            rep.m1_retention_candidates.append({
                "path": plan.get("source"),
                "lifecycle_class": cid,
                "retention_review_after": plan.get("retention_review_after"),
                "size": plan.get("size"),
                "action": "NOTIFY_ONLY",
            })

        if completed and plan.get("destination"):
            archived_batches.append(plan)

        # m3: P0 human deliverable with no Drive pointer yet
        if cid == "P0" and completed and not plan.get("google_drive_pointer"):
            rep.m3_missing_drive_p0.append({
                "path": plan.get("source"),
                "note": "P0 human deliverable pending Google Drive copy",
            })

    # m2: archived P0/P1 batches with no recorded second copy. There is no
    # live N2 mirror yet (registry: N2 NOT_YET_ACCEPTED), so every archived
    # P0/P1 is reported as missing a second copy until one exists.
    for plan in archived_batches:
        if plan.get("lifecycle_class") in ("P0", "P1"):
            rep.m2_missing_second_copy.append({
                "path": plan.get("source"),
                "lifecycle_class": plan.get("lifecycle_class"),
                "note": "no N2 mirror / second copy recorded",
            })

    # m4: Trash entries past the >=30d recoverability window. Read-only list;
    # the delete primitive (tools/release_exec.py) needs --approve.
    trash_root = core.norm(pol.lifecycle.get("trash_root", ""))
    release_days = int(pol.release.get("retention_days", 30))
    if trash_root and os.path.isdir(trash_root):
        for root, _dirs, names in os.walk(trash_root):
            for n in names:
                fp = os.path.join(root, n)
                try:
                    st = os.lstat(fp)
                except OSError:
                    continue
                age = (core.now_ts() - float(getattr(st, "st_mtime", 0) or 0)) / 86400.0
                if age >= release_days:
                    rep.m4_trash_expiry.append({
                        "path": fp,
                        "age_days": round(age, 1),
                        "action": "CONFIRM_ONLY",
                    })
    rep.m4_trash_expiry.sort(key=lambda x: -x["age_days"])

    # m5: growth/effectiveness — intake bytes vs archive bytes vs trash bytes.
    rep.m5_growth = {
        "intake_batches": len(intake["batches"]),
        "intake_bytes": sum(b.get("total_bytes", 0) for b in intake["batches"]),
        "planned_archive_bytes": sum(p.get("size", 0) for p in intake["plans"]
                                     if p.get("destination")),
        "trash_expiry_count": len(rep.m4_trash_expiry),
        "trash_expiry_bytes": sum(
            (os.path.getsize(r["path"]) if os.path.isfile(r["path"]) else 0)
            for r in rep.m4_trash_expiry),
    }
    return rep


def render(rep: MonthlyReport) -> str:
    lines = ["# 月度回顾 %s" % rep.generated]
    lines.append("")
    lines.append("## Intake 摘要")
    s = rep.intake_summary
    lines.append("  batches=%d  plans=%d  migration_authorized=%s" %
                 (s.get("batches", 0), s.get("plans", 0),
                  s.get("migration_authorized", False)))
    lines.append("")

    lines.append("## M1 保留期候选 (P1/P2 到期，仅通知)")
    for r in rep.m1_retention_candidates:
        lines.append("  [%s] %s  review_after=%s  %s" %
                     (r["lifecycle_class"], r["path"],
                      r["retention_review_after"], core.human_gb(r["size"])))
    if not rep.m1_retention_candidates:
        lines.append("  (无)")
    lines.append("")

    lines.append("## M2 缺第二副本 (已归档 P0/P1)")
    for r in rep.m2_missing_second_copy:
        lines.append("  [%s] %s  %s" % (r["lifecycle_class"], r["path"], r["note"]))
    if not rep.m2_missing_second_copy:
        lines.append("  (无)")
    lines.append("")

    lines.append("## M3 缺 Google Drive P0 (人类交付物)")
    for r in rep.m3_missing_drive_p0:
        lines.append("  %s  %s" % (r["path"], r["note"]))
    if not rep.m3_missing_drive_p0:
        lines.append("  (无)")
    lines.append("")

    lines.append("## M4 Trash 到期 (>=30d，仅确认)")
    for r in rep.m4_trash_expiry:
        lines.append("  %6.1f 天  %s" % (r["age_days"], r["path"]))
    if not rep.m4_trash_expiry:
        lines.append("  (无)")
    lines.append("")

    lines.append("## M5 增长/有效性")
    g = rep.m5_growth
    lines.append("  intake_bytes=%s  planned_archive_bytes=%s  trash_expiry=%d 项 %s" %
                 (core.human_gb(g.get("intake_bytes", 0)),
                  core.human_gb(g.get("planned_archive_bytes", 0)),
                  g.get("trash_expiry_count", 0),
                  core.human_gb(g.get("trash_expiry_bytes", 0))))
    return "\n".join(lines) + "\n"
