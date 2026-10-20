"""State-directory self-governance (P1-1): compute a gzip/retention plan.

This module is READ-ONLY by design (PRD I1: the ``gov/`` package owns no delete
primitive). It only inspects the tool's own ``.state/`` tree and returns a list
of {action, path, reason} entries. The actual gzip/delete is performed by
``tools/housekeep_exec.py``, which lives OUTSIDE the package exactly like
``tools/purge_fixture.py``.

Layout managed (all under ``<state_dir>/``):
  inventory/   big per-scan NDJSON       -> keep newest <keep> hot, gzip the rest,
                                            delete > retention
  snapshot/    per-scan JSON snapshots   -> same policy
  candidates/  per-day JSONL             -> same policy
  evidence/    per-day NDJSON            -> same policy (small, but still rotated)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import core


# directories we manage, in priority of "worth compressing first"
MANAGED_DIRS = ("inventory", "snapshot", "candidates", "evidence")


@dataclass
class HousekeepPlan:
    actions: list = field(default_factory=list)   # each: {action, path, reason}
    scanned: int = 0
    would_compress: int = 0
    would_delete: int = 0
    estimated_free: int = 0                        # bytes, raw files only

    def as_dict(self) -> dict:
        return {"scanned": self.scanned,
                "would_compress": self.would_compress,
                "would_delete": self.would_delete,
                "estimated_free": core.human_gb(self.estimated_free),
                "actions": self.actions}


def _mtime_days(path: str) -> float:
    return (core.now_ts() - os.path.getmtime(path)) / 86400.0


def _siblings_sorted(d: str) -> list[str]:
    """All raw data files in a managed dir, oldest first."""
    if not os.path.isdir(d):
        return []
    out = [os.path.join(d, n) for n in os.listdir(d)
           if n.endswith((".ndjson", ".json", ".jsonl"))
           and not n.endswith(".gz")]
    out.sort(key=os.path.getmtime)
    return out


def plan(pol, *, keep: int = 5, retention_days: float = 180.0) -> HousekeepPlan:
    """Compute the governance plan. Pure read; never writes anything."""
    hp = HousekeepPlan()
    base = core.norm(pol.state_dir)

    for name in MANAGED_DIRS:
        d = os.path.join(base, name)
        files = _siblings_sorted(d)
        hp.scanned += len(files)

        by_mtime = sorted(files, key=os.path.getmtime, reverse=True)
        hot = set(by_mtime[:max(keep, 0)])
        rest = [f for f in files if f not in hot]

        for f in rest:
            old = _mtime_days(f) > retention_days
            if old:
                hp.would_delete += 1
                hp.estimated_free += os.path.getsize(f)
                hp.actions.append({"action": "delete", "path": f,
                                   "reason": "past retention %.0fd" % _mtime_days(f)})
                gz = f + ".gz"
                if os.path.exists(gz):
                    hp.estimated_free += os.path.getsize(gz)
                    hp.actions.append({"action": "delete", "path": gz,
                                       "reason": "gz twin past retention"})
            else:
                hp.would_compress += 1
                hp.estimated_free += int(os.path.getsize(f) * 0.8)  # gzip ~20% of text
                hp.actions.append({"action": "gzip", "path": f,
                                   "reason": "beyond hot window (keep=%d)" % keep})

        # orphaned .gz past retention (raw already gone)
        if os.path.isdir(d):
            for n in os.listdir(d):
                if n.endswith(".gz"):
                    p = os.path.join(d, n)
                    if _mtime_days(p) > retention_days:
                        hp.would_delete += 1
                        hp.estimated_free += os.path.getsize(p)
                        hp.actions.append({"action": "delete", "path": p,
                                           "reason": "orphaned gz past retention"})

    return hp
