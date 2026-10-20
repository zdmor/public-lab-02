"""One-time 24-hour re-measurement (FileOrganizer-24h-Recheck task).

Measures C:/D: free space, compares against the most recent purge run
summary and the disk baseline, and appends one line to
.state/purge/recheck.log. This is the "did the release survive the next 24
hours of new logs/caches" check the review asked for: the log line states
both the absolute free space and the delta versus the purge footer.

Usage: python tools/recheck_24h.py
"""
from __future__ import annotations

import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, ".state", "purge", "recheck.log")
JOURNAL = os.path.join(REPO, ".state", "purge", "journal.ndjson")


def last_summary() -> dict:
    last = {}
    if not os.path.isfile(JOURNAL):
        return last
    with open(JOURNAL, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "summary":
                last = r
    return last


def main() -> int:
    import shutil
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "volumes": {}}
    for v in ("C:", "D:"):
        u = shutil.disk_usage(v + os.sep)
        row["volumes"][v] = {"total": u.total, "used": u.used, "free": u.free}
    s = last_summary()
    if s:
        row["vs_purge_run"] = s.get("run_id")
        row["free_after_purge"] = s.get("free_after")
        row["delta_since_purge"] = {
            v: row["volumes"][v]["free"] - s.get("free_after", {}).get(v, 0)
            for v in ("C:", "D:")}
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("recheck: C free %.2f GiB, D free %.2f GiB%s"
          % (row["volumes"]["C:"]["free"] / 2 ** 30,
             row["volumes"]["D:"]["free"] / 2 ** 30,
             (" (vs %s: C %+.2f, D %+.2f GiB)"
              % (row.get("vs_purge_run"),
                 row["delta_since_purge"]["C:"] / 2 ** 30,
                 row["delta_since_purge"]["D:"] / 2 ** 30))
             if s else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
