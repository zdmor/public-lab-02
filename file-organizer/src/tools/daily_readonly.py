"""Daily READ-ONLY cycle (PHASE_SCAN_AND_BASELINE, 2026-10-19).

The phase decision froze all automatic deletion: this task only measures
volumes, prints the housekeep DRY-RUN plan (no gzip, no delete), and appends
one KPI line. It never writes outside .state and never modifies user content.

Usage: python tools/daily_readonly.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
KPI = os.path.join(REPO, ".state", "ledger", "daily_kpi.ndjson")


def main() -> int:
    import shutil
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "READ_ONLY",
           "volumes": {}}
    for v in ("C:", "D:"):
        u = shutil.disk_usage(v + os.sep)
        row["volumes"][v] = {"total": u.total, "used": u.used, "free": u.free}

    # housekeep plan only (dry-run): no --approve, nothing is written
    r = subprocess.run([PY, "-m", "gov.cli", "housekeep", "--keep", "5",
                        "--retention-days", "180"], capture_output=True,
                       text=True, errors="replace", timeout=600,
                       cwd=REPO)
    row["housekeep_dryrun_rc"] = r.returncode
    row["housekeep_dryrun_tail"] = (r.stdout or "")[-300:]

    # purge engine must stay on HOLD during this phase
    hold = os.path.join(REPO, ".state", "purge", "HOLD")
    row["purge_hold_present"] = os.path.exists(hold)

    os.makedirs(os.path.dirname(KPI), exist_ok=True)
    with open(KPI, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("daily_readonly: C free %.2f GiB, D free %.2f GiB, hold=%s"
          % (row["volumes"]["C:"]["free"] / 2 ** 30,
             row["volumes"]["D:"]["free"] / 2 ** 30,
             row["purge_hold_present"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
