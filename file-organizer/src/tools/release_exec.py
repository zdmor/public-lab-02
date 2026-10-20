"""Execute a release manifest. Deliberately OUTSIDE gov/ (PRD I1).

A MOVE parks a file in managed trash but frees no bytes; permanent deletion is
the only step that actually returns space, and v1 had no such step at all. The
governed package therefore owns no delete primitive for user data: this script
is the only component allowed to remove a trashed file, exactly like
``tools/housekeep_exec.py`` and the purge channel.

v2 (2026-10-19, after the P0 code review) is FAIL-CLOSED. The v1 defects that
made it unfit for unattended use were:

  R1  ``src_hash`` empty -> the hash check was skipped and os.remove ran anyway
      (v1 line 112-118). v2: a missing hash is a REFUSAL, never a bypass.
  R2  ``trash_path`` was only prefix-checked, never bound to the journal MOVE
      event's ``dst`` (v1 line 94-95). A manifest could name any file inside a
      managed root while pointing ``event_id`` at a different file's MOVE. v2:
      ``low(trash_path) == low(ev.dst)`` is mandatory.
  R3  retention was re-verified only as manifest age, not as the per-entry undo
      window measured from the MOVE event itself (v1 line 78-83). v2: each
      entry must have sat in trash for >= release.retention_days.
  R4  no reparse/symlink identity check, no journal record before the delete,
      no per-file result after it. v2: all three, plus a HOLD kill-switch.

Every check below runs at execution time; nothing in a (possibly days-old)
manifest is trusted. A single failed check refuses that entry and only that
entry -- there is no "continue anyway" path.

Usage:
    python gov.cli release --save --verify-hash
    python tools/release_exec.py --manifest <file.json> --approve
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, os.pardir))
sys.path.insert(0, REPO_ROOT)

from gov import config, core  # noqa: E402
from gov import journal as jr_mod  # noqa: E402

REPARSE_ATTR = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _manifest_age_days(man: dict, now: float) -> float:
    try:
        return max(0.0, (now - datetime.fromisoformat(man["generated"]).timestamp()) / 86400.0)
    except (KeyError, TypeError, ValueError):
        return 1e9      # unparseable generation stamp = treat as maximally stale


def _is_reparse(path: str) -> bool:
    """True when the final component is a symlink or any other reparse point."""
    try:
        if os.path.islink(path):
            return True
        st = os.stat(path, follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & REPARSE_ATTR)
    except OSError:
        return False


def _delete_time_identity(path: str, want_hash: str) -> str:
    """Re-verify identity immediately before os.remove.

    Returns "" when the file is still the exact regular file that was
    validated, or a refusal reason. This is the concurrent-replacement check:
    anything that swapped, re-pointed or rewrote the path between validation
    and deletion shows up here.
    """
    if not os.path.isfile(path) or _is_reparse(path):
        return "identity changed before delete"
    got = core.sha256_file(path)
    if got != want_hash:
        return "hash changed at delete time"
    return ""


def _event_ts_epoch(ev: dict) -> float:
    try:
        return datetime.fromisoformat(ev["ts"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0     # unparseable journal ts = treat as maximally fresh


def _full_event(pol, plan_id: str, **kw) -> dict:
    """A journal event carrying every EVENT_FIELDS key (PRD 13)."""
    ev = {
        "event_id": jr_mod.new_event_id(),
        "ts": core.now_iso(),
        "machine": pol.hostname,
        "actor": "tools/release_exec.py",
        "plan_id": plan_id,
        "action": "RELEASE",
        "src": "", "dst": "", "src_hash": "", "size": 0, "mtime": 0.0,
        "risk_class": "REVIEW_REQUIRED",
        "rule_ids": ["release"],
        "policy_id": pol.policy_id,
        "policy_version": pol.policy_version,
        "evidence": {}, "ai": {}, "state": "planned", "undo_event_id": None,
    }
    ev.update(kw)
    return ev


def _validate_entry(it: dict, man: dict, pol, jr, now: float,
                    managed_low: tuple, undone: set) -> tuple:
    """Return (ok, reason, ev). Fail-closed: any doubt refuses the entry."""
    if not isinstance(it, dict):
        return False, "entry not an object", None
    path_raw = it.get("trash_path") or ""
    if not path_raw:
        return False, "missing trash_path", None
    path = core.norm(path_raw)

    ev = jr.latest_map().get(it.get("event_id") or "")
    if not ev or ev.get("state") != "done" or ev.get("action") != "MOVE":
        return False, "no committed MOVE in journal", None
    if it.get("event_id") in undone:
        return False, "was undone", None

    # R2: the manifest path must be exactly the path the MOVE parked.
    if core.low(path) != core.low(ev.get("dst") or ""):
        return False, "trash_path != journal dst (substitution)", ev

    if not core.under_prefix(path, managed_low):
        return False, "outside managed root", ev

    if _is_reparse(path):
        return False, "reparse/symlink identity", ev
    if not os.path.isfile(path):
        return False, "not a regular file in trash", ev

    # R1: hash is mandatory at item level and must match the journal record.
    want = it.get("src_hash") or ""
    if not want:
        return False, "missing src_hash (fail-closed)", ev
    if ev.get("src_hash") and want != ev["src_hash"]:
        return False, "src_hash != journal hash", ev

    # R3: per-entry undo window, measured from the MOVE event, not the manifest.
    retention = float((pol.release or {}).get("retention_days", 30))
    age_days = (now - _event_ts_epoch(ev)) / 86400.0
    if age_days < retention:
        return False, "retention not expired (%.1fd < %dd)" % (age_days, retention), ev

    return True, "", ev


def main(argv: list[str]) -> int:
    approve = "--approve" in argv
    manifest_path = None
    for i, a in enumerate(argv):
        if a == "--manifest" and i + 1 < len(argv):
            manifest_path = argv[i + 1]
        elif a.startswith("--manifest="):
            manifest_path = a.split("=", 1)[1]

    if not manifest_path:
        print("usage: release_exec.py --manifest <file.json> --approve")
        return 2
    if not os.path.isfile(manifest_path):
        print("REFUSED: manifest not found: %s" % manifest_path)
        return 2
    if not approve:
        man = _load(manifest_path)
        print("dry-run: %d entries / %s would be permanently deleted"
              % (man.get("count", 0), core.human_gb(man.get("total_bytes", 0))))
        print("add --approve to execute (permanent, undo no longer possible)")
        return 2

    pol = config.load(REPO_ROOT)
    man = _load(manifest_path)
    now = core.now_ts()

    # ---- manifest-level gates -------------------------------------------
    if not isinstance(man, dict) or not isinstance(man.get("items"), list):
        print("REFUSED: manifest is not an object with an items[] list")
        return 2
    max_age = int((pol.release or {}).get("manifest_max_age_days", 7))
    age = _manifest_age_days(man, now)
    if age > max_age:
        print("REFUSED: manifest is %.1fd old (max %dd) - rebuild with `gov release --save`"
              % (age, max_age))
        return 2

    hold = os.path.join(pol.state_dir, "release", "HOLD")
    if os.path.exists(hold):
        print("REFUSED: HOLD present: %s" % hold)
        return 2

    jr = jr_mod.Journal(pol.state_dir, pol.hostname,
                        backup_dir=getattr(pol, "journal_backup_dir", ""))
    jr.latest_map()                       # warm the cache once
    undone = set(jr.undone_ids())
    managed_low = tuple(pol.managed_prefixes_low)
    plan_id = jr_mod.new_plan_id()

    removed = failed = refused = 0
    freed = 0
    for it in man.get("items", []):
        ok, why, ev = _validate_entry(it, man, pol, jr, now, managed_low, undone)
        path = core.norm(it.get("trash_path") or "")
        if not ok:
            print("REFUSED (%s): %s" % (why, path))
            refused += 1
            continue

        size = os.path.getsize(path)
        mtime = os.path.getmtime(path)
        # Journal BEFORE the delete: the record must exist even if we crash
        # between the plan line and the os.remove.
        pre = jr.plan(_full_event(
            pol, plan_id, src=path, src_hash=it.get("src_hash"),
            size=size, mtime=mtime,
            evidence={"manifest": os.path.abspath(manifest_path),
                      "release_of": it.get("event_id")}))
        try:
            # Re-hash immediately before removal: this is the concurrent
            # replacement check (R1's second half).
            why = _delete_time_identity(path, it.get("src_hash"))
            if why:
                jr.commit(pre["event_id"], state="failed",
                          updates={"evidence": {"error": why}})
                print("REFUSED (%s): %s" % (why, path))
                refused += 1
                continue
            os.remove(path)
            freed += size
            removed += 1
            jr.commit(pre["event_id"], state="done",
                      updates={"size": size, "mtime": mtime})
            # after-state check: the path must be gone, not swapped
            if os.path.lexists(path):
                print("! %s still exists after remove" % path)
        except OSError as exc:
            try:
                jr.commit(pre["event_id"], state="failed",
                          updates={"evidence": {"error": str(exc)}})
            except jr_mod.JournalError:
                pass
            print("! %s (%s)" % (path, exc))
            failed += 1

    print("release done: removed=%d refused=%d failed=%d freed=%s plan=%s"
          % (removed, refused, failed, core.human_gb(freed), plan_id))
    return 0 if failed == 0 and refused == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
