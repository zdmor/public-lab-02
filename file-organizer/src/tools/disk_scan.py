"""Same-time-point full-disk scan of C: and D: -> occupancy buckets.

Built for the 2026-10-19 full-machine assessment (email sections A-D). It is
read-only: it stats files and aggregates, it never opens, moves or deletes
anything.

Design notes (the assessment's hard requirements):
  - reparse points (symlinks, junctions) are RECORDED and not descended into,
    so their targets are not double counted (C:\\Users holds legacy junctions);
  - hardlinks are counted once per (device, inode); repeats are reported
    separately as hardlink_hits, so WinSxS-style sharing is not inflated;
  - long paths (>260 chars, typical under node_modules) are walked through the
    \\\\?\\ prefix instead of being silently skipped;
  - permission errors are counted per directory, never fatal;
  - aggregation is by app/owner bucket + mtime/ctime age bands + extension
    rollup, so the output is tables a human can decide from, not 500k rows.

Usage:
    python tools/disk_scan.py [--state-dir PATH] [--top N]
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STATE = os.path.join(REPO, ".state", "scan")

VOLUMES = ("C:", "D:")
BANDS = ((7, "0-7d"), (30, "7-30d"), (90, "30-90d"),
         (180, "90-180d"), (365, "180-365d"), (10 ** 9, ">365d"))
BAND_NAMES = [b[1] for b in BANDS]

MODEL_EXTS = {"gguf", "safetensors", "ckpt", "pt", "pth", "onnx", "h5",
              "pb", "tflite", "npz", "bin", "pkl", "joblib", "msgpack"}
INSTALLER_EXTS = {"exe", "msi", "msix", "appx", "apk", "dmg"}
ARCHIVE_EXTS = {"zip", "7z", "rar", "tar", "gz", "iso", "cab"}


def band_of(days: float) -> str:
    for limit, name in BANDS:
        if days <= limit:
            return name
    return ">365d"


def bucket_of(vol: str, rel: str) -> str:
    parts = [p for p in rel.split(os.sep) if p]
    if not parts:
        return vol + ":/"
    if vol == "C:":
        head = parts[0]
        if head == "Users" and len(parts) >= 2:
            u, rest = parts[1], parts[2:]
            if not rest:
                return "Users/%s" % u
            if rest[0] == "AppData":
                if len(rest) >= 3:
                    return "Users/%s/AppData/%s/%s" % (u, rest[1], rest[2])
                return "Users/%s/AppData/%s" % (u, rest[1] if len(rest) > 1 else "")
            return "Users/%s/%s" % (u, rest[0])
        if head == "Windows":
            return "Windows/%s" % (parts[1] if len(parts) > 1 else "")
        if head in ("Program Files", "Program Files (x86)"):
            return "%s/%s" % (head, parts[1] if len(parts) > 1 else "")
        if head == "ProgramData":
            return "ProgramData/%s" % (parts[1] if len(parts) > 1 else "")
        return "C:/%s" % head
    # D:
    head = parts[0]
    if head == "DataArea":
        if len(parts) >= 3 and parts[1] == "file-organizer" and parts[2] == ".state":
            return "DataArea/file-organizer/.state"
        if len(parts) >= 2:
            return "DataArea/%s" % parts[1]
    if head == "Data":
        if len(parts) >= 3:
            return "Data/%s/%s" % (parts[1], parts[2])
        return "Data/%s" % (parts[1] if len(parts) > 1 else "")
    if head == "_Trash":
        if len(parts) >= 3:
            return "_Trash/%s/%s" % (parts[1], parts[2])
        return "_Trash/%s" % (parts[1] if len(parts) > 1 else "")
    return "D:/%s" % head


class Bucket:
    __slots__ = ("name", "vol", "files", "bytes", "hl_hits", "hl_bytes",
                 "reparse", "mtime_bands", "ctime_bands", "exts",
                 "oldest", "newest", "dir_errors", "dirs_visited",
                 "no_identity")

    def __init__(self, name: str, vol: str):
        self.name, self.vol = name, vol
        self.files = self.bytes = 0
        self.hl_hits = self.hl_bytes = self.reparse = 0
        self.mtime_bands = defaultdict(int)
        self.ctime_bands = defaultdict(int)
        self.exts = defaultdict(int)
        self.oldest, self.newest = float("inf"), 0.0
        self.dir_errors = 0
        self.dirs_visited = 0
        self.no_identity = 0

    def visit(self) -> None:
        """Count a directory that was successfully opened at least once."""
        self.dirs_visited += 1

    def add(self, size: int, mtime: float, ctime: float, ext: str, now: float):
        self.files += 1
        self.bytes += size
        self.mtime_bands[band_of((now - mtime) / 86400.0)] += size
        self.ctime_bands[band_of((now - ctime) / 86400.0)] += size
        if ext:
            self.exts[ext] += size
        self.oldest = min(self.oldest, mtime)
        self.newest = max(self.newest, mtime)

    def as_dict(self) -> dict:
        return {
            "bucket": self.name, "vol": self.vol, "files": self.files,
            "bytes": self.bytes, "hardlink_hits": self.hl_hits,
            "hardlink_bytes": self.hl_bytes, "reparse_points": self.reparse,
            "mtime_bands": {n: self.mtime_bands.get(n, 0) for n in BAND_NAMES},
            "ctime_bands": {n: self.ctime_bands.get(n, 0) for n in BAND_NAMES},
            "top_exts": sorted(self.exts.items(), key=lambda kv: -kv[1])[:6],
            "oldest_mtime": None if self.oldest == float("inf") else self.oldest,
            "newest_mtime": self.newest,
            "dir_errors": self.dir_errors,
            "dirs_visited": self.dirs_visited,
            "no_identity": self.no_identity,
        }


def b_of_dir(d: str, prefix_len: int, vol: str) -> str:
    """Bucket for a directory path (used for per-bucket coverage)."""
    rel = d[prefix_len:] if len(d) > prefix_len else ""
    return bucket_of(vol, rel)


def walk_volume(vol: str, B, seen_inodes: set, stats: dict,
                now: float, denied: list) -> None:
    root = "\\\\?\\" + vol + os.sep
    prefix_len = len(root)
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                stats["dirs_visited"] += 1
                B(b_of_dir(d, prefix_len, vol), vol).visit()
                for e in it:
                    p = e.path
                    try:
                        isj = getattr(e, "is_junction", None)
                        if e.is_symlink() or (isj is not None and isj()):
                            b = bucket_of(vol, p[prefix_len:])
                            B(b, vol).reparse += 1
                            stats["reparse"] += 1
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(p)
                            continue
                        # NB: DirEntry.stat() does NOT populate st_dev/st_ino
                        # on Windows (both come back 0, which would collapse
                        # every file into one "hardlink"). os.stat(path) opens
                        # a handle and returns the real file identity.
                        st = os.stat(p)
                    except OSError:
                        stats["stat_errors"] += 1
                        continue
                    key = (st.st_dev, st.st_ino)
                    b = bucket_of(vol, p[prefix_len:])
                    # (0, 0) means os.stat() could not obtain file identity --
                    # observed on C:\pagefile.sys and C:\swapfile.sys, which the
                    # system holds open. Treating (0,0) as "already seen" would
                    # silently drop every such file from the byte total as if it
                    # were a hardlink (pagefile.sys alone is ~16.3 GiB, which
                    # would land in the residual and be mis-attributed to
                    # unmeasurable system structures). Count them normally and
                    # record the blind spot explicitly.
                    if key == (0, 0):
                        stats["no_identity"] += 1
                        B(b, vol).no_identity += 1
                    elif key in seen_inodes:
                        B(b, vol).hl_hits += 1
                        B(b, vol).hl_bytes += st.st_size
                        stats["hardlink_hits"] += 1
                        continue
                    else:
                        seen_inodes.add(key)
                    ext = os.path.splitext(e.name)[1].lstrip(".").lower()[:12]
                    B(b, vol).add(st.st_size, st.st_mtime, st.st_ctime, ext, now)
                    stats["files"] += 1
                    stats["bytes"] += st.st_size
        except OSError as exc:
            rel = d[prefix_len:] if len(d) > prefix_len else d
            b = bucket_of(vol, rel)
            B(b, vol).dir_errors += 1
            stats["dir_errors"] += 1
            # Coverage metric (review P0-3): a denied directory is unmeasured
            # space, so the path has to be on record -- "501 denials" alone
            # says nothing about how many GiB may be hiding behind them.
            denied.append({"path": vol + os.sep + rel.replace("\\", "/"),
                           "winerror": getattr(exc, "winerror", None),
                           "errno": exc.errno})


def main() -> int:
    state_dir = DEFAULT_STATE
    top_n = 30
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--state-dir" and i + 1 < len(argv):
            state_dir = argv[i + 1]
        if a == "--top" and i + 1 < len(argv):
            top_n = int(argv[i + 1])

    now = time.time()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    buckets: dict = {}

    def B(name: str, vol: str) -> Bucket:
        b = buckets.get(name)
        if b is None:
            b = buckets[name] = Bucket(name, vol)
        return b

    all_stats = {}
    t0 = time.time()
    denied: list = []
    for vol in VOLUMES:
        stats = {"files": 0, "bytes": 0, "reparse": 0, "hardlink_hits": 0,
                 "dir_errors": 0, "stat_errors": 0, "dirs_visited": 0,
                 "no_identity": 0}
        walk_volume(vol, B, set(), stats, now, denied)
        all_stats[vol] = stats
        print("%s: files=%d bytes=%.2f GiB reparse=%d hl_hits=%d dir_errors=%d "
              "dirs_visited=%d no_identity=%d (%.0fs)"
              % (vol, stats["files"], stats["bytes"] / 2 ** 30, stats["reparse"],
                 stats["hardlink_hits"], stats["dir_errors"],
                 stats["dirs_visited"], stats["no_identity"],
                 time.time() - t0))

    # coverage: denied directories are the blind spot behind the residual
    denied_fn = os.path.join(state_dir, "%s_dir_errors.jsonl" % stamp)
    with open(denied_fn, "w", encoding="utf-8") as f:
        for d in denied:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    rows = [b.as_dict() for b in buckets.values()]
    rows.sort(key=lambda r: -r["bytes"])

    os.makedirs(state_dir, exist_ok=True)
    jsonl = os.path.join(state_dir, "%s_buckets.jsonl" % stamp)
    with open(jsonl, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    md = os.path.join(state_dir, "%s_summary.md" % stamp)
    with open(md, "w", encoding="utf-8") as f:
        f.write("# Same-time-point disk scan %s (UTC %s)\n\n"
                % (stamp, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        f.write("## volumes\n\n| vol | files | bytes(GiB) | reparse | hardlink_hits | dir_errors |\n")
        f.write("|---|---|---|---|---|---|\n")
        for v, s in all_stats.items():
            f.write("| %s | %d | %.2f | %d | %d | %d |\n"
                    % (v, s["files"], s["bytes"] / 2 ** 30, s["reparse"],
                       s["hardlink_hits"], s["dir_errors"]))
        f.write("\n## top %d occupancy buckets\n\n" % top_n)
        f.write("| # | bucket | vol | GiB | files | mtime 0-7d | 7-30d | 30-90d | 90-180d | 180-365d | >365d | top exts |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows[:top_n], 1):
            mb = r["mtime_bands"]
            exts = ",".join("%s:%.1fG" % (e, b / 2 ** 30) for e, b in r["top_exts"][:3])
            f.write("| %d | %s | %s | %.2f | %d | %.1f | %.1f | %.1f | %.1f | %.1f | %.1f | %s |\n"
                    % (i, r["bucket"], r["vol"], r["bytes"] / 2 ** 30, r["files"],
                       mb["0-7d"] / 2 ** 30, mb["7-30d"] / 2 ** 30, mb["30-90d"] / 2 ** 30,
                       mb["90-180d"] / 2 ** 30, mb["180-365d"] / 2 ** 30,
                       mb[">365d"] / 2 ** 30, exts))
        f.write("\n## age distribution (all volumes, GiB by mtime band)\n\n")
        f.write("| band | GiB |\n|---|---|\n")
        tot = defaultdict(int)
        for r in rows:
            for n, v in r["mtime_bands"].items():
                tot[n] += v
        for n in BAND_NAMES:
            f.write("| %s | %.2f |\n" % (n, tot[n] / 2 ** 30))
        f.write("\n## model/installer/archive extension rollup (all volumes)\n\n")
        f.write("| class | GiB |\n|---|---|\n")
        cls_tot = defaultdict(int)
        for r in rows:
            for e, b in r["top_exts"]:
                if e in MODEL_EXTS:
                    cls_tot["model:" + e] += b
                elif e in INSTALLER_EXTS:
                    cls_tot["installer:" + e] += b
                elif e in ARCHIVE_EXTS:
                    cls_tot["archive:" + e] += b
        for k in sorted(cls_tot, key=lambda k: -cls_tot[k])[:15]:
            f.write("| %s | %.2f |\n" % (k, cls_tot[k] / 2 ** 30))
        f.write("\n## reparse / hardlink / error totals\n\n")
        f.write("- reparse points recorded (not descended): %d\n"
                % sum(s["reparse"] for s in all_stats.values()))
        f.write("- hardlink repeat hits (counted once): %d\n"
                % sum(s["hardlink_hits"] for s in all_stats.values()))
        f.write("- directory permission errors: %d\n"
                % sum(s["dir_errors"] for s in all_stats.values()))
        f.write("- stat errors: %d\n" % sum(s["stat_errors"] for s in all_stats.values()))
        f.write("\n## coverage (what the scan could and could not see)\n\n")
        f.write("| vol | dirs visited | files measured | dir denials | "
                "denials per 1k visited dirs |\n|---|---|---|---|---|\n")
        for v, s in all_stats.items():
            per_k = (1000.0 * s["dir_errors"] / s["dirs_visited"]
                     if s["dirs_visited"] else 0.0)
            f.write("| %s | %d | %d | %d | %.1f |\n"
                    % (v, s["dirs_visited"], s["files"], s["dir_errors"], per_k))
        # group denials by 3-segment prefix so the blind spot is attributable
        g = defaultdict(int)
        for d in denied:
            p = d["path"].lower().replace("\\", "/")
            seg = "/".join(p.split("/")[:4])
            g[seg] += 1
        f.write("\ndenied directories by 3-seg prefix (top 15):\n\n")
        f.write("| prefix | denials |\n|---|---|\n")
        for k in sorted(g, key=lambda k: -g[k])[:15]:
            f.write("| %s | %d |\n" % (k, g[k]))
        f.write("\nfull denial list: `%s`\n" % os.path.basename(denied_fn))

    print("buckets=%d jsonl=%s summary=%s denied=%d (%.0fs total)"
          % (len(rows), jsonl, md, len(denied), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
