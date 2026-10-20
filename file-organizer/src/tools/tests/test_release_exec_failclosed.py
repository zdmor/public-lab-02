"""Negative tests for tools/release_exec.py v2 (fail-closed).

Every defect the P0 review found in v1 must REFUSE here. A test that stops
refusing is a regression; these run without touching the real .state journal
(a throwaway temp state dir is used instead).

Run:  python tools/tests/test_release_exec_failclosed.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
sys.path.insert(0, REPO)

import release_exec as rx  # noqa: E402
from gov import core, journal as jr_mod  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name + ((" :: " + detail) if detail and not cond else ""))


def fake_pol(state_dir: str) -> SimpleNamespace:
    return SimpleNamespace(
        release={"retention_days": 30, "manifest_max_age_days": 7},
        hostname="TEST-HOST", policy_id="test", policy_version="0",
        state_dir=state_dir, managed_prefixes_low=(), journal_backup_dir="")


def plant_move(jr, pol, dst: str, src_hash: str, days_ago: float,
               state: str = "done") -> dict:
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    ev = jr.plan(rx._full_event(
        pol, jr_mod.new_plan_id(), action="MOVE", state="planned",
        src=dst + ".origin", dst=dst, src_hash=src_hash, size=10, mtime=0.0, ts=ts))
    if state != "planned":
        jr.commit(ev["event_id"], state=state, updates={"ts": ts})
    return ev


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="relx_neg_")
    try:
        pol = fake_pol(tmp)
        jr = jr_mod.Journal(tmp, "TEST-HOST")
        jr.latest_map()
        trash_root = os.path.join(tmp, "trash")
        os.makedirs(trash_root, exist_ok=True)

        good = os.path.join(trash_root, "good.bin")
        with open(good, "wb") as f:
            f.write(b"x" * 10)
        good_hash = core.sha256_file(good)
        managed = (core.low(trash_root),)
        undone_ids: set = set()
        now = core.now_ts()

        # baseline: a properly aged, hashed, done MOVE must PASS validation
        ev_ok = plant_move(jr, pol, good, good_hash, days_ago=40)
        ok, why, _ = rx._validate_entry(
            {"event_id": ev_ok["event_id"], "trash_path": good, "src_hash": good_hash},
            {}, pol, jr, now, managed, undone_ids)
        check("happy path validates", ok, why)

        # N1 missing src_hash -> REFUSED (v1 skipped the hash and deleted)
        ok, why, _ = rx._validate_entry(
            {"event_id": ev_ok["event_id"], "trash_path": good, "src_hash": ""},
            {}, pol, jr, now, managed, undone_ids)
        check("N1 missing hash refused", not ok and "hash" in why, why)

        # N2 substitution: manifest names another managed file, event points at good
        other = os.path.join(trash_root, "other.bin")
        with open(other, "wb") as f:
            f.write(b"y" * 10)
        ok, why, _ = rx._validate_entry(
            {"event_id": ev_ok["event_id"], "trash_path": other,
             "src_hash": core.sha256_file(other)},
            {}, pol, jr, now, managed, undone_ids)
        check("N2 path substitution refused", not ok and "substitution" in why, why)

        # N3 retention not expired (moved 2 days ago)
        ev_fresh = plant_move(jr, pol, good, good_hash, days_ago=2)
        ok, why, _ = rx._validate_entry(
            {"event_id": ev_fresh["event_id"], "trash_path": good, "src_hash": good_hash},
            {}, pol, jr, now, managed, undone_ids)
        check("N3 unexpired retention refused", not ok and "retention" in why, why)

        # N4 undone MOVE -> REFUSED
        ev_undone = plant_move(jr, pol, good, good_hash, days_ago=40)
        jr.append(rx._full_event(pol, jr_mod.new_plan_id(), action="UNDO",
                                 undo_of=ev_undone["event_id"], state="done"))
        ok, why, _ = rx._validate_entry(
            {"event_id": ev_undone["event_id"], "trash_path": good, "src_hash": good_hash},
            {}, pol, jr, now, managed, undone_ids | {ev_undone["event_id"]})
        check("N4 undone event refused", not ok and "undone" in why, why)

        # N5 event never committed (planned) -> REFUSED
        ev_plan = plant_move(jr, pol, good, good_hash, days_ago=40, state="planned")
        ok, why, _ = rx._validate_entry(
            {"event_id": ev_plan["event_id"], "trash_path": good, "src_hash": good_hash},
            {}, pol, jr, now, managed, undone_ids)
        check("N5 uncommitted MOVE refused", not ok and "MOVE" in why, why)

        # N6 reparse identity: a junction standing in for the trashed file
        jdir = os.path.join(trash_root, "as_dir")
        real = os.path.join(tmp, "real_dir")
        os.makedirs(real, exist_ok=True)
        r = subprocess.run(["cmd", "/c", "mklink", "/J", jdir, real],
                           capture_output=True, errors="replace")
        if r.returncode == 0:
            # the journal MOVE must name the junction itself, so the
            # substitution gate passes and the reparse gate is what fires
            ev_j = plant_move(jr, pol, jdir, good_hash, days_ago=40)
            ok, why, _ = rx._validate_entry(
                {"event_id": ev_j["event_id"], "trash_path": jdir,
                 "src_hash": good_hash},
                {}, pol, jr, now, managed, undone_ids)
            check("N6 reparse/junction refused", not ok and "reparse" in why, why)
        else:
            check("N6 reparse/junction refused (env cannot mklink /J)", True,
                  "skipped: %s" % str(r.stderr)[:60])

        # N7 concurrent replacement between validation and delete
        victim = os.path.join(trash_root, "swap.bin")
        with open(victim, "wb") as f:
            f.write(b"a" * 10)
        vh = core.sha256_file(victim)
        check("N7 delete-time identity clean at first", rx._delete_time_identity(victim, vh) == "")
        with open(victim, "wb") as f:      # someone rewrites the file in between
            f.write(b"b" * 10)
        why = rx._delete_time_identity(victim, vh)
        check("N7 concurrent replacement refused", "hash changed" in why, why)

        # N8 outside managed root (journal dst must match, so the root gate fires)
        outside = os.path.join(tmp, "outside.bin")
        with open(outside, "wb") as f:
            f.write(b"z" * 10)
        oh = core.sha256_file(outside)
        ev_out = plant_move(jr, pol, outside, oh, days_ago=40)
        ok, why, _ = rx._validate_entry(
            {"event_id": ev_out["event_id"], "trash_path": outside,
             "src_hash": oh},
            {}, pol, jr, now, managed, undone_ids)
        check("N8 outside managed root refused", not ok and "managed" in why, why)

        # N9 manifest age gate
        man = {"generated": (datetime.now() - timedelta(days=30)).isoformat(),
               "items": []}
        age = rx._manifest_age_days(man, now)
        check("N9 stale manifest age detected", age > 7, "%.1f" % age)
        man_bad = {"items": []}
        age2 = rx._manifest_age_days(man_bad, now)
        check("N9 unparseable manifest age is maximal", age2 > 1e8, "%f" % age2)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("release_exec fail-closed negative tests: %d passed, %d failed"
          % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAIL", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
