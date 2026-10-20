"""The only module with write side effects: MOVE / RENAME / TAG_SIDECAR.

Every action is pre-registered in the journal before the filesystem sees it
and finalized afterwards (PRD I5 / DESIGN 3.5). Nothing here ever deletes:
there is no deletion primitive available in this package (PRD I1).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from . import core, journal as journal_mod, tags as tags_mod
from .policy import references as blocking_references

ACTION_KINDS = ("MOVE", "RENAME", "TAG_SIDECAR", "UNDO")
REJECT_CODES = ("not_safe", "too_long", "collision", "referenced", "never_rename",
                "frozen_zone", "cross_volume", "not_approved", "budget", "missing_source",
                "review_only", "noop", "volume_root_target")


class ExecuteError(Exception):
    pass


@dataclass
class Action:
    plan_id: str
    event_id: str
    action: str
    src: str
    dst: str
    risk_class: str
    rule_ids: list
    reason: str
    size: int = 0
    mtime: float = 0.0
    reject: Optional[str] = None
    src_hash: Optional[str] = None
    ai: Optional[dict] = None
    manual: bool = False

    def as_dict(self) -> dict:
        return {"plan_id": self.plan_id, "event_id": self.event_id,
                "action": self.action, "src": self.src, "dst": self.dst,
                "risk_class": self.risk_class, "rule_ids": list(self.rule_ids),
                "reason": self.reason, "size": self.size, "mtime": self.mtime,
                "reject": self.reject, "src_hash": self.src_hash, "ai": self.ai,
                "manual": self.manual}


def _base_event(pol, act: Action, actor: str) -> dict:
    return {
        "event_id": act.event_id, "ts": core.now_iso(), "machine": core.machine_name(),
        "actor": actor, "plan_id": act.plan_id, "action": act.action,
        "src": act.src, "dst": act.dst, "src_hash": act.src_hash, "size": act.size,
        "mtime": act.mtime, "risk_class": act.risk_class, "rule_ids": act.rule_ids,
        "policy_id": pol.policy_id, "policy_version": pol.policy_version,
        "evidence": {"reason": act.reason}, "ai": act.ai, "state": "planned",
        "undo_event_id": None,
    }


def managed_trash_dest(pol, src: str, plan_id: str, roots: Sequence[str]) -> str:
    """<managed root on the SOURCE volume>/trash/<machine>/<date>/<plan_id>/<rel>.

    A MOVE must stay on one volume (cross_volume reject), so the destination is
    picked per source drive from ``pol.managed_roots``; a volume without a
    declared root fails closed here and the candidate is rejected instead.
    """
    rel = core.norm(src)
    for root in sorted(roots, key=lambda r: -len(core.norm(r))):
        # core.prefixed, not startswith: a declared "D:\" root ends with a separator.
        if core.prefixed(core.low(src), core.low(root)):
            rel = core.norm(src)[len(core.norm(root)):].lstrip("\\/")
            break
    if not rel:
        raise ExecuteError("candidate is the declared root itself: %s" % src)
    base = pol.managed_roots.get(core.drive_of(src).lower())
    if not base:
        raise ExecuteError("no managed root declared on volume %s for: %s"
                           % (core.drive_of(src), src))
    return core.norm(os.path.join(base, "trash", core.machine_name(),
                                  core.date_str(), plan_id, rel))


def plan(pol, cands: Sequence, *, kind: str = "MOVE", roots: Sequence[str] = (),
         tags_fn: Optional[Callable] = None, approved_ids: Optional[set] = None,
         limit: Optional[int] = None, refs=None) -> tuple[str, list[Action]]:
    """Build an action plan. Pure: it never touches the filesystem for writes."""
    if kind not in ("MOVE", "RENAME", "TAG_SIDECAR"):
        raise ExecuteError("unknown plan kind %r" % kind)
    plan_id = journal_mod.new_plan_id()
    limit = limit if limit is not None else pol.max_actions_per_run
    actions: list[Action] = []
    # One action per source path, always. A file reachable through two declared
    # scan roots (e.g. D:\ plus a narrower root inside it) lands in the
    # candidate list twice, and emitting both would make the second MOVE fail
    # on a source that the first one already moved. Dedup by normalized source,
    # keeping the largest record, and report what was collapsed instead of
    # silently shrinking the plan.
    seen_src: dict = {}
    dup_paths = 0
    for cand in cands:
        key = core.norm(cand.path).lower()
        prev = seen_src.get(key)
        if prev is not None:
            dup_paths += 1
            if int(cand.size or 0) > int(getattr(prev, "size", 0) or 0):
                seen_src[key] = cand
            continue
        seen_src[key] = cand
    ordered = sorted(seen_src.values(),
                     key=lambda c: -int(getattr(c, "size", 0) or 0))
    for cand in ordered:
        if len(actions) >= limit:
            actions.append(Action(plan_id, "budget_marker", kind, "", "",
                                  cand.risk_class, [], "budget",
                                  reject="budget"))
            break
        event_id = journal_mod.new_event_id()
        if kind == "MOVE":
            if cand.risk_class != core.RISK_CLASSES[0]:
                continue
            if approved_ids is not None and core.norm(cand.path) not in approved_ids:
                actions.append(Action(plan_id, event_id, kind, cand.path, "",
                                      cand.risk_class, cand.rule_ids,
                                      "not in approval set", cand.size, cand.mtime,
                                      reject="not_approved"))
                continue
            try:
                dst = managed_trash_dest(pol, cand.path, plan_id, roots)
            except Exception:
                actions.append(Action(plan_id, event_id, kind, cand.path, "",
                                      cand.risk_class, cand.rule_ids, "dest_unresolved",
                                      cand.size, cand.mtime, reject="not_safe"))
                continue
            act = Action(plan_id, event_id, kind, core.norm(cand.path), dst,
                         cand.risk_class, cand.rule_ids, "cleanup_rule_matched",
                         cand.size, cand.mtime)
            act.reject = _move_reject(act, pol, refs)
        else:
            tgt_tags = (tags_fn(cand) if tags_fn else []) or []
            new_path, reason = tags_mod.plan_rename(cand.path, tgt_tags, pol, refs)
            act = Action(plan_id, event_id, kind, core.norm(cand.path),
                         core.norm(new_path) if new_path else core.norm(cand.path),
                         cand.risk_class, cand.rule_ids,
                         reason or "tag_applied", cand.size, cand.mtime)
            act.reject = None if new_path else (reason or "not_safe")
            if kind == "TAG_SIDECAR":
                act.dst = tags_mod.sidecar_path(act.src)
                act.reject = _sidecar_reject(act, pol, refs)
        actions.append(act)
    if dup_paths:
        # Recorded, never silently dropped: a plan whose candidate input had
        # duplicate sources is worth knowing about at apply time.
        actions.append(Action(plan_id, "dedup_marker", kind, "", "",
                              "INFO", [], "dedup",
                              reject="dedup:%d duplicate candidate paths collapsed" % dup_paths))
    return plan_id, actions


def _move_reject(act: Action, pol, refs=None) -> Optional[str]:
    if not os.path.lexists(act.src):
        return "missing_source"
    if core.drive_of(act.src) != core.drive_of(act.dst):
        return "cross_volume"
    blocked = pol.action_target_ok(os.path.dirname(act.dst))
    if blocked:
        return "frozen_zone" if "frozen" in blocked else "not_safe"
    if os.path.lexists(act.dst):
        return "collision"
    reason = core.check_long_path(act.dst, pol.max_target_path)
    if reason:
        return "too_long"
    if refs is not None:
        # Same semantics as the decision layer (policy.references): inside a
        # declared regenerable zone the env/uninst/reparse container inference
        # is overridden, but exact file hits and runtime kinds still block.
        blocking, _ignored = blocking_references(act.src, refs, pol)
        if blocking:
            return "referenced"
    return None


def _sidecar_reject(act: Action, pol, refs=None) -> Optional[str]:
    if not os.path.lexists(act.src):
        return "missing_source"
    if os.path.lexists(act.dst):
        return "collision"
    if core.check_long_path(act.dst, pol.max_target_path):
        return "too_long"
    blocked = pol.action_target_ok(os.path.dirname(act.src))
    if blocked:
        return "frozen_zone" if "frozen" in blocked else "not_safe"
    return None


def write_sidecar(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(core.norm(path)), exist_ok=True)
    tmp = core.norm(path)
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))


def apply_plan(pol, actions: Sequence[Action], jr: journal_mod.Journal, *,
               approved: bool, actor: str = "cli", hash_files: bool = False,
               dry_run: bool = False) -> dict:
    """Execute an approved plan. Without approved=True nothing moves."""
    if not approved and not dry_run:
        raise ExecuteError("apply refused: approval flag missing (PRD FR6)")
    res = {"plan_id": actions[0].plan_id if actions else "", "done": 0,
           "skipped": [], "failed": [], "dry_run": dry_run, "bytes_moved": 0}
    for act in actions:
        if act.event_id == "budget_marker":
            res["skipped"].append([act.reason, "plan truncated by max_actions_per_run"])
            continue
        if act.reject:
            res["skipped"].append([act.src, act.reject])
            continue
        if act.risk_class != core.RISK_CLASSES[0]:
            # A rule-classified AUTO_SAFE action is the only automatic path. A
            # manually tagged RENAME (PRD FR8, `gov tag --approve`) is allowed
            # only when the operator approved it in this very invocation.
            if not (act.manual and approved and act.action == "RENAME"):
                res["skipped"].append([act.src, "not_approved_risk_class"])
                continue
            if not os.path.lexists(act.src):
                res["skipped"].append([act.src, "missing_source"])
                continue
            if os.path.lexists(act.dst):
                res["skipped"].append([act.src, "collision"])
                continue
        if dry_run:
            res["done"] += 1
            res["bytes_moved"] += act.size
            continue
        ev = _base_event(pol, act, actor)
        if hash_files and act.size and act.size <= 64 * 1024 * 1024:
            ev["src_hash"] = core.sha256_file(act.src)
        jr.plan(ev)
        try:
            if act.action == "MOVE":
                os.makedirs(os.path.dirname(act.dst), exist_ok=True)
                os.rename(act.src, act.dst)
                _metadata_for(pol, act, ev, jr)
            elif act.action == "RENAME":
                os.rename(act.src, act.dst)
            elif act.action == "TAG_SIDECAR":
                write_sidecar(act.dst, {"src": act.src, "tags": act.rule_ids,
                                        "risk_class": act.risk_class,
                                        "event_id": act.event_id,
                                        "plan_id": act.plan_id,
                                        "policy": [pol.policy_id, pol.policy_version],
                                        "ts": core.now_iso()})
            jr.commit(act.event_id, "done", {"src_hash": ev.get("src_hash")})
            res["done"] += 1
            res["bytes_moved"] += act.size
        except OSError as exc:
            jr.commit(act.event_id, "failed",
                      {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
            res["failed"].append([act.src, type(exc).__name__, str(exc)[:120]])
    return res


def _metadata_for(pol, act: Action, ev: dict, jr: journal_mod.Journal) -> None:
    meta = {"original_path": act.src, "moved_at": core.now_iso(),
            "plan_id": act.plan_id, "event_id": act.event_id,
            "risk_class": act.risk_class, "rule_ids": act.rule_ids,
            "evidence": ev.get("evidence"), "size": act.size, "mtime": act.mtime,
            "policy": [pol.policy_id, pol.policy_version]}
    try:
        write_sidecar(os.path.join(os.path.dirname(act.dst), ".gov_meta.json"), meta)
    except OSError:
        pass


def undo_event(pol, jr: journal_mod.Journal, event_id: str, *, approved: bool,
               actor: str = "cli", dry_run: bool = False) -> dict:
    """Reverse one MOVE/RENAME. No cascade: undoing X does not undo later actions."""
    if not approved and not dry_run:
        raise ExecuteError("undo refused: approval flag missing")
    ev = jr.get(event_id)
    if not ev:
        return {"ok": False, "reason": "unknown_event"}
    if ev.get("state") != "done":
        return {"ok": False, "reason": "state_%s" % ev.get("state")}
    if ev.get("action") not in ("MOVE", "RENAME"):
        return {"ok": False, "reason": "undo_of_%s" % ev.get("action")}
    if event_id in jr.undone_ids():
        return {"ok": False, "reason": "already_undone"}
    src, dst = core.norm(ev["dst"]), core.norm(ev["src"])
    if not os.path.lexists(src):
        return {"ok": False, "reason": "missing_current"}
    if os.path.lexists(dst):
        return {"ok": False, "reason": "collision"}
    if core.drive_of(src) != core.drive_of(dst):
        return {"ok": False, "reason": "cross_volume"}
    blocked = pol.action_target_ok(os.path.dirname(dst))
    if blocked:
        return {"ok": False, "reason": "frozen_zone" if "frozen" in blocked else "not_safe"}
    if core.check_long_path(dst, pol.max_target_path):
        return {"ok": False, "reason": "too_long"}
    new_id = journal_mod.new_event_id()
    back = dict(ev)
    back.update({"event_id": new_id, "action": "UNDO", "undo_of": event_id,
                 "src": src, "dst": dst, "state": "planned", "undo_event_id": event_id,
                 "ts": core.now_iso()})
    if dry_run:
        return {"ok": True, "dry_run": True, "would_undo": event_id, "new_event_id": new_id}
    jr.plan(back)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.rename(src, dst)
    except OSError as exc:
        jr.commit(new_id, "failed", {"error": str(exc)[:200]})
        return {"ok": False, "reason": "failed", "error": str(exc)[:200]}
    jr.commit(new_id, "done")
    jr.commit(event_id, "undone", {"undo_event_id": new_id})
    return {"ok": True, "event_id": new_id, "undone": event_id, "restored": dst}


def undo_plan(pol, jr: journal_mod.Journal, plan_id: str, *, approved: bool,
              actor: str = "cli", dry_run: bool = False) -> list[dict]:
    out = []
    for ev in reversed(jr.by_plan(plan_id)):
        if ev.get("state") == "done" and ev.get("action") in ("MOVE", "RENAME"):
            out.append(undo_event(pol, jr, ev["event_id"], approved=approved,
                                  actor=actor, dry_run=dry_run))
    return out
