#!/usr/bin/env python3
"""Bracketed same-time-point measurement (review P0-4).

The audit flagged that the Win32 used/free reading (21:14) and the recursive
enumeration (20:46) were not taken at the same instant, while the machine keeps
writing 100-230 MB/min. This tool brackets a full enumeration with volume
readings taken immediately before and immediately after, records the drift, and
reports the midpoint as the best same-time-point estimate. Read-only.

Usage: python tools/volume_bracket.py
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIR = os.path.join(REPO, ".state", "scan")
VOLUMES = ("C:\\", "D:\\")


def readings() -> dict:
    out = {}
    for v in VOLUMES:
        u = shutil.disk_usage(v)
        out[v[0]] = {"total": u.total, "used": u.used, "free": u.free}
    return out


def main() -> int:
    os.makedirs(SCAN_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    t0_wall = time.time()
    before = readings()
    t0_iso = time.strftime("%Y-%m-%dT%H:%M:%S")

    r = subprocess.run([sys.executable, os.path.join("tools", "disk_scan.py")],
                       cwd=REPO, capture_output=True, text=True,
                       errors="replace", timeout=3600)
    after = readings()
    t1_wall = time.time()
    t1_iso = time.strftime("%Y-%m-%dT%H:%M:%S")

    newest = max(glob.glob(os.path.join(SCAN_DIR, "*_buckets.jsonl")),
                 key=os.path.getmtime, default="")

    # enumerated bytes from the buckets file
    enum = {}
    if newest:
        for line in open(newest, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            vol = d.get("vol", "?")[0]
            cell = enum.setdefault(vol, {"files": 0, "bytes": 0,
                                         "hardlink_hits": 0,
                                         "dir_errors": 0})
            cell["files"] += d.get("files", 0)
            cell["bytes"] += d.get("bytes", 0)
            cell["hardlink_hits"] += d.get("hardlink_hits", 0)
            cell["dir_errors"] += d.get("dir_errors", 0)

    res = {
        "stamp": stamp,
        "scan_start_local": t0_iso,
        "scan_end_local": t1_iso,
        "scan_seconds": round(t1_wall - t0_wall, 1),
        "buckets_file": os.path.basename(newest),
        "volumes": {},
        "disk_scan_rc": r.returncode,
        "disk_scan_tail": (r.stdout or "")[-400:],
    }
    for v in ("C", "D"):
        b, a = before[v], after[v]
        res["volumes"][v] = {
            "used_before": b["used"], "used_after": a["used"],
            "free_before": b["free"], "free_after": a["free"],
            "total": b["total"],
            "used_mid": (b["used"] + a["used"]) // 2,
            "free_mid": (b["free"] + a["free"]) // 2,
            "used_drift_bytes": a["used"] - b["used"],
            "enumerated_bytes": enum.get(v, {}).get("bytes", 0),
            "enumerated_files": enum.get(v, {}).get("files", 0),
            "hardlink_hits": enum.get(v, {}).get("hardlink_hits", 0),
            "dir_errors": enum.get(v, {}).get("dir_errors", 0),
        }
    for v in ("C", "D"):
        c = res["volumes"][v]
        c["residual_mid_GiB"] = round((c["used_mid"] - c["enumerated_bytes"]) / 2 ** 30, 3)
        c["used_before_GiB"] = round(c["used_before"] / 2 ** 30, 3)
        c["used_after_GiB"] = round(c["used_after"] / 2 ** 30, 3)
        c["used_mid_GiB"] = round(c["used_mid"] / 2 ** 30, 3)
        c["enumerated_GiB"] = round(c["enumerated_bytes"] / 2 ** 30, 3)
        c["drift_MiB"] = round(c["used_drift_bytes"] / 2 ** 20, 2)

    out = os.path.join(SCAN_DIR, "%s_volume_bracket.json" % stamp)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res["volumes"], ensure_ascii=False, indent=1))
    print("scan_seconds=%s buckets=%s -> %s"
          % (res["scan_seconds"], os.path.basename(newest), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
