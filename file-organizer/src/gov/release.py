"""P1 release closure: what has sat in managed trash past the retention window.

v1 only ever MOVEds. A file parked in managed trash still occupies the volume,
so nothing is actually recovered until the trash is emptied — the value chain
(perceive -> classify -> decide -> act -> release) had no last link. This
module is the read side of that link: it turns "what has been sitting in the
trash past the undo window" into an explicit manifest a human can approve as a
whole or reject.

It contains no delete primitive (PRD I1). The delete lives outside the package
in tools/release_exec.py, which re-validates every entry and refuses to run
without --approve.

An entry only reaches the manifest when all of these hold (fail-closed):
  - the journal holds a committed `done` MOVE for it. No journal record means
    it never happened, whatever the filesystem claims.
  - no UNDO rolled that event back; an undone file is back where it came from
    and must never be touched here.
  - the trash copy still exists and sits under a declared managed root, so a
    path that merely looks like trash cannot be deleted through this door.
  - it has been in trash at least release.retention_days.
With verify_hash=True the copy must also still hash to the src_hash recorded at
apply time; a mismatch is counted as skipped, never offered for deletion.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from . import core

DEFAULT_RETENTION_DAYS = 30
DEFAULT_MANIFEST_MAX_AGE_DAYS = 7


@dataclass
class ReleaseItem:
    event_id: str
    plan_id: str
    src: str
    trash_path: str
    size: int
    trashed_days: float
    src_hash: str = ""
    sha256_ok: object = None          # None = not verified in this pass

    def as_dict(self) -> dict:
        return {"event_id": self.event_id, "plan_id": self.plan_id,
                "src": self.src, "trash_path": self.trash_path,
                "size": self.size, "trashed_days": round(self.trashed_days, 2),
                "src_hash": self.src_hash, "sha256_ok": self.sha256_ok}


@dataclass
class ReleasePlan:
    generated: str = ""
    retention_days: int = DEFAULT_RETENTION_DAYS
    manifest_max_age_days: int = DEFAULT_MANIFEST_MAX_AGE_DAYS
    verified: bool = False
    items: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(int(i.size or 0) for i in self.items)

    def as_dict(self) -> dict:
        return {"kind": "release_manifest", "generated": self.generated,
                "retention_days": self.retention_days,
                "manifest_max_age_days": self.manifest_max_age_days,
                "verified": self.verified, "total_bytes": self.total_bytes,
                "count": len(self.items), "skipped": dict(self.skipped),
                "items": [i.as_dict() for i in self.items]}


def _age_days(ev: dict, now: float) -> float:
    ts = ev.get("ts") or ""
    try:
        return max(0.0, (now - datetime.fromisoformat(ts).timestamp()) / 86400.0)
    except (TypeError, ValueError):
        pass
    mtime = ev.get("mtime")
    if isinstance(mtime, (int, float)) and mtime:
        return max(0.0, (now - float(mtime)) / 86400.0)
    return 0.0


def build(pol, jr, *, now: float = None, verify_hash: bool = False) -> ReleasePlan:
    """Read-only: turn committed, un-undone, expired trash MOVE events into a
    manifest. Never touches the filesystem for writes; with verify_hash it only
    reads (hashing) the trash copies."""
    rel_cfg = dict(getattr(pol, "release", {}) or {})
    plan = ReleasePlan(
        generated=core.now_iso(),
        retention_days=int(rel_cfg.get("retention_days") or DEFAULT_RETENTION_DAYS),
        manifest_max_age_days=int(rel_cfg.get("manifest_max_age_days")
                                  or DEFAULT_MANIFEST_MAX_AGE_DAYS),
        verified=bool(verify_hash),
    )
    now = float(now if now is not None else core.now_ts())
    managed_low = tuple(pol.managed_prefixes_low)
    undone = set(jr.undone_ids())
    skip = Counter()

    for ev in jr.latest_map().values():
        if ev.get("action") != "MOVE" or ev.get("state") != "done":
            continue
        if ev.get("event_id") in undone:
            skip["undone_back_at_source"] += 1
            continue
        dst = core.norm(ev.get("dst") or "")
        if not dst or not core.under_prefix(dst, managed_low):
            skip["outside_managed_root"] += 1
            continue
        age = _age_days(ev, now)
        if age < plan.retention_days:
            skip["inside_undo_window"] += 1
            continue
        if not os.path.isfile(dst):
            skip["missing_in_trash"] += 1
            continue
        item = ReleaseItem(
            event_id=ev.get("event_id") or "", plan_id=ev.get("plan_id") or "",
            src=core.norm(ev.get("src") or ""), trash_path=dst,
            size=os.path.getsize(dst), trashed_days=age,
            src_hash=ev.get("src_hash") or "",
        )
        if verify_hash:
            digest = core.sha256_file(dst) if item.src_hash else None
            item.sha256_ok = bool(digest) and digest == item.src_hash
            if not item.sha256_ok:
                skip["hash_mismatch_or_unreadable"] += 1
                continue
        plan.items.append(item)

    plan.items.sort(key=lambda i: -int(i.size or 0))
    plan.skipped = dict(skip)
    return plan


def render(plan: ReleasePlan, *, verify_hint: bool = False) -> str:
    lines = ["# 释放清单 (managed trash 滞留到期) %s" % plan.generated]
    lines.append("retention_days = %d | manifest_max_age_days = %d | hash_verified = %s"
                 % (plan.retention_days, plan.manifest_max_age_days,
                    "yes" if plan.verified else "no"))
    lines.append("待释放: %d 项 / %s" % (len(plan.items), core.human_gb(plan.total_bytes)))
    lines.append("")
    if not plan.items:
        lines.append("(无到期条目：trash 为空，或全部仍在 undo 窗口内)")
    for it in plan.items[:50]:
        lines.append("  %10s  %6.1fd  %s" % (core.human_gb(it.size), it.trashed_days,
                                             it.trash_path))
        lines.append("      <- %s" % it.src)
    if len(plan.items) > 50:
        lines.append("  ... 另有 %d 项未显示" % (len(plan.items) - 50))
    if plan.skipped:
        lines.append("")
        lines.append("未纳入（fail-closed 计数）:")
        for k, v in sorted(plan.skipped.items()):
            lines.append("  %-32s %d" % (k, v))
    lines.append("")
    lines.append("删除原语不在 gov/ 内：本清单必须由 tools/release_exec.py --approve 执行。")
    if not verify_hint:
        lines.append("提示: 加 --verify-hash 生成前逐个核对 sha256（更慢，建议正式释放时使用）。")
    return "\n".join(lines)
