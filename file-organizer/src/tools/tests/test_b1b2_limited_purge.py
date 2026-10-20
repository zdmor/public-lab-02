"""Regression tests for src/tools/b1b2_limited_purge.py.

Covers the five review requirements:
  1. path containment (junction escape must not delete outside the root)
  2. reparse points are never traversed or removed, including by dir pruning
  3. per-file and total byte budgets
  4. files held open by another handle are recorded FAILED, not deleted
  5. updater roots refuse to purge until the installed version >= staged version

Run:  python src/tools/tests/test_b1b2_limited_purge.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import b1b2_limited_purge as ex  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name + ((" :: " + detail) if detail and not cond else ""))


def write(path: str, payload: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


def make_junction(link: str, target: str) -> bool:
    """Create a directory junction (no admin required)."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", link, target],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def run_roots(roots, run_dir, limits, probe=None):
    if probe is None:
        probe = lambda _root_id: None  # noqa: E731
    return ex.run(roots, run_dir, dry_run=False, limits=limits, probe=probe,
                  now=time.time())


def journal_codes(run_dir: str) -> list[str]:
    with open(os.path.join(run_dir, "journal.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line)["result"] for line in fh if line.strip()]


def test_basic_delete(tmp: str) -> None:
    root = os.path.join(tmp, "cache")
    target = write(os.path.join(root, "a.bin"), b"x" * 8)
    summary = run_roots(
        [ex.Root("C01_test", root)], os.path.join(tmp, "run1"),
        ex.Limits(per_file_cap=1024, total_cap=4096, min_age_seconds=0),
    )
    check("basic: file deleted", not os.path.exists(target))
    check("basic: bytes counted", summary["deleted_bytes"] == 8, str(summary["deleted_bytes"]))


def test_containment_and_reparse(tmp: str) -> None:
    root = os.path.join(tmp, "cache_j")
    outside = os.path.join(tmp, "outside")
    secret = write(os.path.join(outside, "secret.bin"), b"do-not-touch")
    os.makedirs(root, exist_ok=True)
    link = os.path.join(root, "escape")
    made = make_junction(link, outside)
    check("reparse: junction created", made)
    if not made:
        return
    summary = run_roots(
        [ex.Root("C01_test", root)], os.path.join(tmp, "run2"),
        ex.Limits(per_file_cap=1024, total_cap=4096, min_age_seconds=0),
    )
    check("containment: outside file survives", os.path.exists(secret))
    check("reparse: junction survives", os.path.isdir(link))
    check("containment: nothing deleted", summary["deleted_files"] == 0,
          str(summary["deleted_files"]))
    os.rmdir(link)


def test_prune_skips_reparse(tmp: str) -> None:
    root = os.path.join(tmp, "prune_root")
    inside = os.path.join(root, "empty")
    target = os.path.join(tmp, "prune_target")
    os.makedirs(inside, exist_ok=True)
    os.makedirs(target, exist_ok=True)
    link = os.path.join(inside, "link")
    made = make_junction(link, target)
    check("prune: junction created", made)
    if not made:
        return
    removed = ex.prune_empty_dirs(root)
    check("prune: junction not removed", os.path.isdir(link))
    check("prune: empty parent retained", os.path.isdir(inside))
    check("prune: no dirs reported", removed == 0, str(removed))
    os.rmdir(link)


def test_budgets(tmp: str) -> None:
    root = os.path.join(tmp, "budget")
    big = write(os.path.join(root, "big.bin"), b"B" * 20)
    small = write(os.path.join(root, "small.bin"), b"s" * 5)
    run_dir = os.path.join(tmp, "run3")
    summary = run_roots(
        [ex.Root("C01_test", root)], run_dir,
        ex.Limits(per_file_cap=10, total_cap=1024, min_age_seconds=0),
    )
    codes = journal_codes(run_dir)
    check("budget: per-file cap skips big", os.path.exists(big))
    check("budget: small deleted", not os.path.exists(small))
    check("budget: SKIP_OVER_CAP recorded", "SKIP_OVER_CAP" in codes, ",".join(codes))

    root2 = os.path.join(tmp, "budget2")
    paths = [write(os.path.join(root2, "f%d.bin" % i), b"1234") for i in range(3)]
    summary2 = run_roots(
        [ex.Root("C02_test", root2)], os.path.join(tmp, "run4"),
        ex.Limits(per_file_cap=1024, total_cap=6, min_age_seconds=0),
    )
    check("budget: total cap deletes at most 6 B", summary2["deleted_bytes"] <= 6,
          str(summary2["deleted_bytes"]))
    check("budget: exactly one file deleted", summary2["deleted_files"] == 1,
          str(summary2["deleted_files"]))
    check("budget: remaining files intact",
          sum(1 for p in paths if os.path.exists(p)) == 2)


def test_file_in_use(tmp: str) -> None:
    root = os.path.join(tmp, "inuse")
    path = write(os.path.join(root, "locked.bin"), b"locked")
    run_dir = os.path.join(tmp, "run5")
    handle = open(path, "rb")
    try:
        summary = run_roots(
            [ex.Root("C01_test", root)], run_dir,
            ex.Limits(per_file_cap=1024, total_cap=4096, min_age_seconds=0),
        )
    finally:
        handle.close()
    check("in-use: recorded FAILED", summary["failed_files"] == 1,
          str(summary["failed_files"]))
    check("in-use: file survives", os.path.exists(path))
    check("in-use: FAILED in journal", "FAILED" in journal_codes(run_dir))


def test_min_age(tmp: str) -> None:
    root = os.path.join(tmp, "recent")
    path = write(os.path.join(root, "fresh.bin"), b"fresh")
    run_dir = os.path.join(tmp, "run6")
    summary = run_roots(
        [ex.Root("C01_test", root)], run_dir,
        ex.Limits(per_file_cap=1024, total_cap=4096, min_age_seconds=600),
    )
    check("min-age: recent file skipped", os.path.exists(path))
    check("min-age: SKIP_RECENT recorded", "SKIP_RECENT" in journal_codes(run_dir))
    check("min-age: nothing deleted", summary["deleted_files"] == 0)


def make_updater(tmp: str, name: str, staged_version: str) -> str:
    root = os.path.join(tmp, name)
    write(os.path.join(root, "pending", "update-info.json"),
          json.dumps({"version": staged_version}).encode())
    write(os.path.join(root, "pending", "app-%s-win-x64.exe" % staged_version), b"P" * 32)
    return root


def test_pending_gate(tmp: str) -> None:
    root = make_updater(tmp, "updater_old", "2.1.7")
    run_dir = os.path.join(tmp, "run7")
    summary = run_roots(
        [ex.Root("C08_test", root, requires_completed_upgrade=True)], run_dir,
        ex.Limits(per_file_cap=1024, total_cap=4096, min_age_seconds=0),
        probe=lambda _root_id: "2.1.6",
    )
    stats = summary["roots"]["C08_test"]
    check("gate: older installed version refused",
          stats["status"] == "REFUSED_PENDING_UPGRADE", stats["status"])
    check("gate: refusal reason", stats.get("reason") == "UPGRADE_NOT_INSTALLED",
          str(stats.get("reason")))
    check("gate: staged file survives",
          os.path.exists(os.path.join(root, "pending", "app-2.1.7-win-x64.exe")))
    check("gate: refused root counted", summary["gate_refused_roots"] == 1)

    root2 = make_updater(tmp, "updater_equal", "2.1.7")
    summary2 = run_roots(
        [ex.Root("C08_test", root2, requires_completed_upgrade=True)],
        os.path.join(tmp, "run8"),
        ex.Limits(per_file_cap=1024, total_cap=4096, min_age_seconds=0),
        probe=lambda _root_id: "2.1.7",
    )
    check("gate: installed equal allows purge", summary2["deleted_files"] == 2,
          str(summary2["deleted_files"]))

    root3 = make_updater(tmp, "updater_unknown", "2.1.7")
    summary3 = run_roots(
        [ex.Root("C08_test", root3, requires_completed_upgrade=True)],
        os.path.join(tmp, "run9"),
        ex.Limits(per_file_cap=1024, total_cap=4096, min_age_seconds=0),
        probe=lambda _root_id: None,
    )
    stats3 = summary3["roots"]["C08_test"]
    check("gate: unknown installed version refused",
          stats3["status"] == "REFUSED_PENDING_UPGRADE", stats3["status"])
    check("gate: unknown reason", stats3.get("reason") == "INSTALLED_VERSION_UNKNOWN",
          str(stats3.get("reason")))


def test_version_compare(tmp: str) -> None:
    check("version: 2.1.6 < 2.1.7", not ex.version_at_least("2.1.6", "2.1.7"))
    check("version: 2.1.7 == 2.1.7", ex.version_at_least("2.1.7", "2.1.7"))
    check("version: 2.1.8 > 2.1.7", ex.version_at_least("2.1.8", "2.1.7"))
    check("version: 0.2.0-rc.2 < 0.2.1-rc.2",
          not ex.version_at_least("0.2.0-rc.2", "0.2.1-rc.2"))
    check("version: release > same prerelease",
          ex.version_at_least("0.2.1", "0.2.1-rc.2"))


def test_safe_to_delete_reparse(tmp: str) -> None:
    root = os.path.join(tmp, "safe_root")
    outside = os.path.join(tmp, "safe_outside")
    os.makedirs(root, exist_ok=True)
    os.makedirs(outside, exist_ok=True)
    link = os.path.join(root, "link")
    if not make_junction(link, outside):
        check("safe-delete: junction created", False)
        return
    ok, why = ex.safe_to_delete(os.path.realpath(root), link,
                                ex.Limits(1024, 4096, 0))
    check("safe-delete: reparse refused", not ok)
    check("safe-delete: reason REPARSE", why == "REPARSE", why)
    os.rmdir(link)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="b1b2_test_")
    try:
        for fn in (test_basic_delete, test_containment_and_reparse,
                   test_prune_skips_reparse, test_budgets, test_file_in_use,
                   test_min_age, test_pending_gate, test_version_compare,
                   test_safe_to_delete_reparse):
            try:
                fn(tmp)
            except Exception as exc:  # noqa: BLE001 - test harness reports all
                check(fn.__name__ + ": exception", False, repr(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("PASS %d  FAIL %d" % (len(PASS), len(FAIL)))
    for name in PASS:
        print("  PASS", name)
    for name in FAIL:
        print("  FAIL", name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
