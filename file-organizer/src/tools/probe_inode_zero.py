#!/usr/bin/env python3
"""Probe: do any files report st_ino == 0 / duplicate (dev,ino)?

The bucket scan de-duplicates on (st_dev, st_ino) so hardlinks are counted
once. If os.stat() hands out ino == 0 for some files (it does for some system
files opened through \\\\?\\ paths), every one of them after the first is
silently dropped from the byte total as a "hardlink". C:\\pagefile.sys landed
in that bucket in scan 20261019_234747 (files=0, bytes=0, hardlink_hits=1),
which is a 16.3 GiB accounting hole. Read-only.
"""
from __future__ import annotations

import json
import os
import sys

TARGETS = [
    "C:\\pagefile.sys",
    "C:\\swapfile.sys",
    "C:\\hiberfil.sys",
    "C:\\Windows\\System32\\ntoskrnl.exe",
    "C:\\Windows\\WinSxS\\amd64_microsoft-windows-os-kernel_"
    "31bf3856ad364e35_10.0.26100.1_none_0000000000000000\\ntoskrnl.exe",
]


def stat(path: str):
    longp = "\\\\?\\" + path
    try:
        st = os.stat(longp)
    except OSError as exc:
        try:
            st = os.stat(path)
        except OSError as exc2:
            return None, "%s / %s" % (exc, exc2)
    return st, None


def main() -> int:
    print("== direct stat ==")
    for t in TARGETS:
        st, err = stat(t)
        if st is None:
            print("  %-40s ERR %s" % (t, err))
            continue
        print("  %-40s dev=%s ino=%s nlink=%s size=%.3f GiB"
              % (os.path.basename(t), st.st_dev, st.st_ino,
                 getattr(st, "st_nlink", "?"), st.st_size / 2 ** 30))

    # How many files in the latest candidates snapshot would collide?
    fn = None
    import glob
    cands = sorted(glob.glob(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".state", "candidates", "*-candidates.jsonl")))
    if cands:
        fn = cands[-1]
    if fn and len(sys.argv) > 1 and sys.argv[1] == "--scan-snapshot":
        print("\n== (dev,ino) collisions in %s ==" % os.path.basename(fn))
        seen = {}
        zero = 0
        collide = 0
        collide_bytes = 0
        for line in open(fn, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            p = d.get("path")
            if not p:
                continue
            st, err = stat(p)
            if st is None:
                continue
            if st.st_ino == 0:
                zero += 1
                collide_bytes += st.st_size
                continue
            k = (st.st_dev, st.st_ino)
            if k in seen:
                collide += 1
            else:
                seen[k] = p
        print("  ino==0 entries: %d (%.3f GiB)" % (zero, collide_bytes / 2 ** 30))
        print("  true duplicate (dev,ino): %d" % collide)
    return 0


if __name__ == "__main__":
    sys.exit(main())
