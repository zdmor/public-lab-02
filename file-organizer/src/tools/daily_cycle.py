"""Daily natural cycle: measure volumes, housekeep own state, append KPI.

This is the body of the FileOrganizer-DailyCycle scheduled task. It never
deletes user data: housekeep only gzips/rotates the tool's OWN .state
snapshots (journal/evidence excluded by policy), and the purge engine is only
reported on, not run, here. One NDJSON line per day keeps the GiB-release
history deduplicated and comparable.

Usage: python tools/daily_cycle.py
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
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "volumes": {}}
    for v in ("C:", "D:"):
        u = shutil.disk_usage(v + os.sep)
        row["volumes"][v] = {"total": u.total, "used": u.used, "free": u.free}

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(REPO, ".state", "housekeep", "hk_daily_%s.json" % stamp)
    r = subprocess.run([PY, os.path.join(REPO, "tools", "housekeep_exec.py"),
                        "--approve", "--keep", "5", "--retention-days", "180",
                        "--json", out], capture_output=True, text=True,
                       errors="replace", timeout=1800)
    row["housekeep_rc"] = r.returncode
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as f:
            hk = json.load(f)
        row["housekeep"] = {k: hk.get(k) for k in
                            ("actions", "raw_bytes_removed", "gz_bytes_created",
                             "net_bytes_delta", "free_bytes_before",
                             "free_bytes_after", "free_bytes_delta")}

    os.makedirs(os.path.dirname(KPI), exist_ok=True)
    with open(KPI, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("daily_cycle: C free %.2f GiB, D free %.2f GiB, housekeep rc=%d"
          % (row["volumes"]["C:"]["free"] / 2 ** 30,
             row["volumes"]["D:"]["free"] / 2 ** 30, r.returncode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
