#!/usr/bin/env python3
"""Backfill manifests for existing candidates snapshots (PHASE_SCAN_BASELINE §5).

The manifest writer landed in gov/cli.py after these snapshots were taken, so
their roots_requested is NOT recorded (marked null, backfilled=true). Everything
else (hash, entries, volume coverage, as_of from the filename stamp) is computed
from the artifact itself.
"""
import glob
import gzip
import hashlib
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from gov import core  # noqa: E402


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_rows(fn):
    if fn.endswith(".gz"):
        with gzip.open(fn, "rt", encoding="utf-8") as f:
            for line in f:
                yield line
    else:
        with open(fn, encoding="utf-8") as f:
            for line in f:
                yield line


def backfill(fn: str) -> dict:
    vols = {}
    total = 0
    n = 0
    for line in iter_rows(fn):
        try:
            d = json.loads(line)
        except Exception:
            continue
        n += 1
        p = d.get("path", "")
        sz = d.get("size", 0)
        vol = os.path.splitdrive(core.norm(p))[0].upper() + ":"
        cell = vols.setdefault(vol, {"entries": 0, "bytes": 0})
        cell["entries"] += 1
        cell["bytes"] += sz
        total += sz
    base = os.path.basename(fn)
    if base.endswith(".jsonl.gz"):
        stamp = base.replace(".jsonl.gz", "")
        as_of_epoch = time.mktime(time.strptime(stamp, "%Y-%m-%d"))
    else:
        stamp = base.split("-")[0]
        as_of_epoch = time.mktime(time.strptime(stamp, "%Y%m%d_%H%M%S"))
    manifest = {
        "schema_version": 1,
        "scan_id": stamp,
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(as_of_epoch)),
        "as_of_epoch": as_of_epoch,
        "as_of_precision": "date-only (from filename)" if base.endswith(".gz")
                          else "exact (from filename)",
        "snapshot_path": os.path.abspath(fn),
        "manifest_hash": sha256_file(fn),
        "entry_count": n,
        "total_bytes": total,
        "volume_coverage": vols,
        "roots_requested": None,
        "backfilled": True,
        "backfilled_at": core.now_iso(),
        "generator": "tools/backfill_manifests.py",
    }
    mpath = fn + ".manifest.json"
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest


def main() -> int:
    files = sorted(glob.glob(os.path.join(REPO, ".state", "candidates", "*")))
    files = [f for f in files
             if f.endswith(".jsonl") or f.endswith(".jsonl.gz")]
    for fn in files:
        m = backfill(fn)
        print("%-46s entries=%-8d %8.2f GiB hash=%s..."
              % (os.path.basename(fn), m["entry_count"],
                 m["total_bytes"] / 2 ** 30, m["manifest_hash"][:12]))
    print("backfilled %d manifests" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
