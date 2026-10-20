"""snapshot_diff.py -- same-caliber diff between two gov candidates snapshots.

Built for the PHASE_SCAN_BASELINE phase decision (2026-10-19): "两份真实快照
同口径 diff". Both snapshots are real full-volume candidates enumerations.

EPISTEMIC CONTRACT (revision 2026-10-20, audit SCAN_R3_REVIEW_20261020 item 1)
------------------------------------------------------------------------------
A path absent from the old path-index but present in the new index verifies a
NEWLY OBSERVED PATH. It does not, by itself, verify new content. Classes are
therefore named after what the evidence actually supports, and every class
carries an explicit `proves` / `does_not_prove` pair:

  PATH_ADDED_RECENT_MTIME   path absent in OLD and mtime >= old_as_of
                        -> a newly observed path whose mtime falls inside the
                           window: bytes were written there during the window
  PATH_ADDED_BACKDATED_MTIME
                        path absent in OLD and mtime <  old_as_of
                        -> a path the old enumeration did not record; the
                           content's age and provenance are UNKNOWN
  CONTENT_NEW_VERIFIED     RESERVED -- deliberately never emitted by this tool.
                        Only content identity (st_dev/st_ino) or provenance
                        (hashes, creation events, USN journal) can support it,
                        and candidates snapshots carry neither. Kept in the
                        schema so the claim cannot be silently reintroduced.
  SIZE_MTIME_CHANGED    (size, mtime) tuple moved for a path present in both
                        -> metadata/size differ; content difference not proven
  PATH_REMOVED_VERIFIED path present in OLD, absent in NEW, outside caliber
                        artifacts -> the path is no longer observable
  PATH_GONE_CALIBER     path vanished because the scan caliber changed
                        (root/frozen-prefix) -> NOT a deletion
  POSSIBLE_MOVED        a (size, mtime) match between a gone and an added path
                        -> HEURISTIC. No file identity (inode) or content hash
                           is available in candidates snapshots, so a (size,
                           mtime) collision is suggestive, not probative.

The index net delta (`net_index_delta`) is snapshot-index arithmetic. It is
NOT evidence of physical disk growth; that requires a matched volume-level
used-bytes baseline (see tools/volume_bracket.py) taken over the same window.

Caliber caveat handled explicitly: a scan-root/frozen-prefix change between
the two runs produces REMOVED entries that are NOT real deletions (e.g.
D:\\DataArea\\ChatClient was frozen on 2026-10-19, so its 18k entries
vanish from the new snapshot). Such entries are tagged PATH_GONE_CALIBER and
kept out of the real-removal byte accounting.

Usage:
    python tools/snapshot_diff.py --old A.jsonl --new B.jsonl \
        [--artifact-prefix "d:\\dataarea\\chatclient"] ... \
        [--out-dir .state/scan] [--tag TAG] \
        [--volume-baseline .state/scan/<stamp>_volume_bracket.json]

Outputs (all read-only w.r.t. the snapshots themselves):
    <stamp>_snapdiff_<tag>_summary.json   machine-readable aggregates
    <stamp>_snapdiff_<tag>_summary.md     human-readable tables
    <stamp>_snapdiff_<tag>_detail.jsonl   per-entry classified changes
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

from disk_scan import BANDS, BAND_NAMES, band_of, bucket_of  # noqa: E402

DEFAULT_OUT = os.path.join(REPO, ".state", "scan")
DEFAULT_ARTIFACTS = ("d:\\dataarea\\chatclient",)

SIZE_BANDS = ((1024, "0-1KiB"), (1024 ** 2, "1KiB-1MiB"),
              (10 * 1024 ** 2, "1-10MiB"), (100 * 1024 ** 2, "10-100MiB"),
              (1024 ** 3, "100MiB-1GiB"), (float("inf"), ">1GiB"))
SIZE_BAND_NAMES = [b[1] for b in SIZE_BANDS]


def size_band_of(n: int) -> str:
    for limit, name in SIZE_BANDS:
        if n <= limit:
            return name
    return ">1GiB"


CLASS_META = {
    "PATH_ADDED_RECENT_MTIME": {
        "proves": "path absent from OLD snapshot AND mtime >= old_as_of: a "
                  "newly observed path whose mtime falls inside the window, so "
                  "bytes were written there during the window",
        "does_not_prove": "that the bytes are unique or newly created -- a "
                          "copy/restore or re-download from outside the "
                          "scanned roots also yields a fresh mtime, and mtime "
                          "can be set arbitrarily. It is a recent-mtime "
                          "subgroup, not a content-identity finding",
        "evidence_basis": "path-index absence + mtime >= old_as_of",
    },
    "PATH_ADDED_BACKDATED_MTIME": {
        "proves": "path absent from OLD snapshot (newly observed path)",
        "does_not_prove": "that the content is new -- mtime predates the old "
                          "snapshot, so this may be a restore, an extraction "
                          "with preserved timestamps, or a move from a path the "
                          "old snapshot did not cover. Content age UNKNOWN",
        "evidence_basis": "path-index absence only",
    },
    "CONTENT_NEW_VERIFIED": {
        "proves": "RESERVED. Requires content identity (st_dev/st_ino) or "
                  "provenance (content hash / USN creation events). Candidates "
                  "snapshots carry neither, so this class is never emitted",
        "does_not_prove": "n/a -- the class is intentionally empty so the "
                          "claim cannot be reintroduced by relabeling a "
                          "path-based finding",
        "evidence_basis": "content identity or provenance only",
    },
    "SIZE_MTIME_CHANGED": {
        "proves": "the (size, mtime) tuple for a path present in both snapshots "
                  "is different",
        "does_not_prove": "that file content changed -- a touch, an in-place "
                          "append of zero bytes, or a metadata rewrite all move "
                          "mtime; no hash is computed",
        "evidence_basis": "size inequality OR mtime inequality",
    },
    "PATH_REMOVED_VERIFIED": {
        "proves": "a path recorded by OLD is no longer observable in NEW",
        "does_not_prove": "permanent deletion -- it may have moved to a path "
                          "outside the scanned roots, or the content may still "
                          "exist in a recycle bin or shadow copy",
        "evidence_basis": "path-index absence in NEW, outside caliber prefixes",
    },
    "PATH_GONE_CALIBER": {
        "proves": "the path disappeared from the index because the scan caliber "
                  "(scan roots / frozen prefixes) changed between runs",
        "does_not_prove": "any deletion. These bytes are EXCLUDED from removal "
                          "accounting",
        "evidence_basis": "path under a declared caliber-artifact prefix",
    },
    "POSSIBLE_MOVED": {
        "proves": "a (size, mtime) tuple matches between one gone path and one "
                  "newly observed path",
        "does_not_prove": "that they are the same file. Candidates snapshots "
                          "carry no file identity (st_dev/st_ino) and no content "
                          "hash, so (size, mtime) is a heuristic that can "
                          "collide. Report as POSSIBLE, never as MOVED",
        "evidence_basis": "heuristic: equal (size, round(mtime,6))",
    },
}


def as_of_from_name(fn: str) -> float:
    """20261011_180412-candidates.jsonl -> local-time epoch."""
    base = os.path.basename(fn)
    stamp = base.split("-")[0]
    t = time.strptime(stamp, "%Y%m%d_%H%M%S")
    return time.mktime(t)


def low(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def under(path_low: str, prefixes_low) -> bool:
    return any(path_low == f or path_low.startswith(f + os.sep)
               for f in prefixes_low)


def load(fn: str) -> dict:
    """path(lower) -> (size, mtime, ext). First occurrence wins."""
    out = {}
    dup = 0
    with open(fn, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            p = d.get("path")
            if not p:
                continue
            k = low(p)
            if k in out:
                dup += 1
                continue
            out[k] = (d.get("size", 0), d.get("mtime", 0.0),
                      (d.get("ext") or "").lower())
    return out, dup


class Agg:
    """Aggregation cell: files/bytes by size band + ext."""

    __slots__ = ("files", "bytes", "bands", "exts")

    def __init__(self):
        self.files = 0
        self.bytes = 0
        self.bands = defaultdict(int)
        self.exts = defaultdict(int)

    def add(self, size: int, ext: str):
        self.files += 1
        self.bytes += size
        self.bands[size_band_of(size)] += size
        if ext:
            self.exts[ext] += size

    def d(self):
        return {"files": self.files, "bytes": self.bytes,
                "size_bands": {n: self.bands.get(n, 0) for n in SIZE_BAND_NAMES},
                "top_exts": sorted(self.exts.items(), key=lambda kv: -kv[1])[:6]}


def main() -> int:
    argv = sys.argv[1:]
    old_fn = new_fn = None
    out_dir = DEFAULT_OUT
    tag = "diff"
    artifacts = list(DEFAULT_ARTIFACTS)
    vol_base_fn = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--old" and i + 1 < len(argv):
            old_fn = argv[i + 1]; i += 2
        elif a == "--new" and i + 1 < len(argv):
            new_fn = argv[i + 1]; i += 2
        elif a == "--out-dir" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 2
        elif a == "--tag" and i + 1 < len(argv):
            tag = argv[i + 1]; i += 2
        elif a == "--artifact-prefix" and i + 1 < len(argv):
            artifacts.append(argv[i + 1]); i += 2
        elif a == "--volume-baseline" and i + 1 < len(argv):
            vol_base_fn = argv[i + 1]; i += 2
        else:
            print("unknown arg: %s" % a)
            return 2
    if not old_fn or not new_fn:
        print("usage: snapshot_diff.py --old A.jsonl --new B.jsonl [--tag T]")
        return 2

    t0 = time.time()
    old_as_of = as_of_from_name(old_fn)
    new_as_of = as_of_from_name(new_fn)
    artifacts_low = tuple(dict.fromkeys(low(x) for x in artifacts))
    print("old=%s as_of=%s" % (os.path.basename(old_fn),
          time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(old_as_of))))

    old, dup_old = load(old_fn)
    print("old loaded: %d paths (dup=%d, %.0fs)" % (len(old), dup_old, time.time() - t0))
    new, dup_new = load(new_fn)
    print("new loaded: %d paths (dup=%d, %.0fs)" % (len(new), dup_new, time.time() - t0))

    # ---- classify -------------------------------------------------------
    added = {}      # path -> (size, mtime, ext); split after moved detection
    modified = {}   # path -> (old_size, new_size, mtime_new, ext)
    unchanged_n = 0
    # INFERRED_RECENT: not provably new, but mtime says recently active.
    # (a) young_unchanged: same size+mtime in both snapshots AND mtime within
    #     7d before the OLD snapshot -- young then, untouched since;
    # (b) modified_in_window: mtime moved inside the snapshot window.
    # NB: an UNCHANGED entry can never have mtime strictly inside the window
    # (its old-snapshot mtime already existed at old_as_of), so (a) is the
    # only meaningful "looks recent, cannot prove new" bucket for unchanged.
    young_unchanged = Agg()
    modified_in_window = Agg()
    for k, v in new.items():
        ov = old.get(k)
        if ov is None:
            added[k] = v
        elif ov[0] != v[0] or ov[1] != v[1]:
            modified[k] = (ov[0], v[0], v[1], v[2])
            if v[1] >= old_as_of:
                modified_in_window.add(v[0], v[2])
        else:
            unchanged_n += 1
            if old_as_of - ov[1] <= 7 * 86400:
                young_unchanged.add(v[0], v[2])
    removed = {k: v for k, v in old.items() if k not in new}

    # ---- moved detection: (size, mtime) match between removed and added ---
    rem_idx = defaultdict(list)
    for k, v in removed.items():
        rem_idx[(v[0], round(v[1], 6))].append(k)
    moved = {}   # new path -> old path
    for k, v in added.items():
        bucket_list = rem_idx.get((v[0], round(v[1], 6)))
        if bucket_list:
            old_path = bucket_list.pop()
            moved[k] = old_path
    for k in moved:
        added.pop(k, None)
        removed.pop(moved[k], None)

    # ---- caliber artifacts among REMOVED --------------------------------
    caliber_excluded = {}
    real_removed = {}
    for k, v in removed.items():
        if under(k, artifacts_low):
            caliber_excluded[k] = v
        else:
            real_removed[k] = v

    # ---- split ADDED by evidence strength (R3 item 1) --------------------
    # PATH_ADDED_RECENT_MTIME: mtime >= old_as_of -> the bytes were written at
    # a newly observed path inside the window. PATH_ADDED_BACKDATED_MTIME:
    # mtime predates the old snapshot -> we only know the path is newly
    # observed; the content may be a restore / preserved-timestamp copy /
    # move-in. CONTENT_NEW_VERIFIED is reserved and never emitted.
    added_recent = {}
    added_backdated = {}
    for k, v in added.items():
        if v[1] >= old_as_of:
            added_recent[k] = v
        else:
            added_backdated[k] = v

    # ---- aggregates -----------------------------------------------------
    def agg_of(entries):
        """entries: iterable of (path, size, mtime, ext)"""
        by_bucket = defaultdict(Agg)
        by_vol = defaultdict(Agg)
        by_age = defaultdict(Agg)
        tot = Agg()
        for k, v in entries:
            size, mtime, ext = v[0], v[1], v[-1]
            vol = k[:2].upper()
            rel = k[3:] if len(k) > 2 else ""
            b = bucket_of(vol, rel.replace("/", os.sep))
            by_bucket[b].add(size, ext)
            by_vol[vol].add(size, ext)
            by_age[band_of((new_as_of - mtime) / 86400.0)].add(size, ext)
            tot.add(size, ext)
        return by_bucket, by_vol, by_age, tot

    # added entries carry (size, mtime, ext); modified carry 4-tuples
    added_items = [(k, (v[0], v[1], v[2])) for k, v in added.items()]
    cn_items = [(k, (v[0], v[1], v[2])) for k, v in added_recent.items()]
    pa_items = [(k, (v[0], v[1], v[2])) for k, v in added_backdated.items()]
    mod_items = [(k, (v[1], v[2], v[3])) for k, v in modified.items()]
    rem_items = [(k, (v[0], v[1], v[2])) for k, v in real_removed.items()]
    cal_items = [(k, (v[0], v[1], v[2])) for k, v in caliber_excluded.items()]
    mv_items = [(k, (new[k][0], new[k][1], "")) for k in moved]
    A_bucket, A_vol, A_age, A_tot = agg_of(added_items)
    CN_bucket, CN_vol, CN_age, CN_tot = agg_of(cn_items)
    PA_bucket, PA_vol, PA_age, PA_tot = agg_of(pa_items)
    M_bucket, M_vol, M_age, M_tot = agg_of(mod_items)
    R_bucket, R_vol, R_age, R_tot = agg_of(rem_items)
    C_bucket, _, _, C_tot = agg_of(cal_items)
    MV_bucket, _, MV_age, MV_tot = agg_of(mv_items)

    # ---- write outputs ---------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.join(out_dir, "%s_snapdiff_%s" % (stamp, tag))

    # detail jsonl
    det = base + "_detail.jsonl"
    with open(det, "w", encoding="utf-8") as f:
        for k, v in added_recent.items():
            f.write(json.dumps({"cls": "PATH_ADDED_RECENT_MTIME", "path": k,
                                "size": v[0], "mtime": v[1], "ext": v[2]},
                               ensure_ascii=False) + "\n")
        for k, v in added_backdated.items():
            f.write(json.dumps({"cls": "PATH_ADDED_BACKDATED_MTIME", "path": k,
                                "size": v[0], "mtime": v[1], "ext": v[2]},
                               ensure_ascii=False) + "\n")
        for k, v in modified.items():
            f.write(json.dumps({"cls": "SIZE_MTIME_CHANGED", "path": k,
                                "size_old": v[0], "size_new": v[1],
                                "mtime": v[2], "ext": v[3]},
                               ensure_ascii=False) + "\n")
        for k, v in real_removed.items():
            f.write(json.dumps({"cls": "PATH_REMOVED_VERIFIED", "path": k,
                                "size": v[0], "mtime": v[1], "ext": v[2]},
                               ensure_ascii=False) + "\n")
        for k, v in caliber_excluded.items():
            f.write(json.dumps({"cls": "PATH_GONE_CALIBER", "path": k,
                                "size": v[0], "mtime": v[1], "ext": v[2]},
                               ensure_ascii=False) + "\n")
        for nk, ok_ in moved.items():
            f.write(json.dumps({"cls": "POSSIBLE_MOVED", "path_new": nk,
                                "path_old": ok_, "size": new[nk][0],
                                "mtime": new[nk][1],
                                "match_basis": "size+round(mtime,6)"},
                               ensure_ascii=False) + "\n")

    # summary json
    def agg_d(aggs):
        return {name: a.d() for name, a in aggs.items()}

    summary = {
        "schema_version": 1,
        "old_snapshot": os.path.abspath(old_fn),
        "new_snapshot": os.path.abspath(new_fn),
        "old_as_of": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(old_as_of)),
        "new_as_of": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(new_as_of)),
        "window_days": (new_as_of - old_as_of) / 86400.0,
        "old_entries": len(old), "new_entries": len(new),
        "old_dup_skipped": dup_old, "new_dup_skipped": dup_new,
        "counts": {
            "PATH_ADDED_RECENT_MTIME": len(added_recent),
            "PATH_ADDED_BACKDATED_MTIME": len(added_backdated),
            "SIZE_MTIME_CHANGED": len(modified),
            "PATH_REMOVED_VERIFIED": len(real_removed),
            "PATH_GONE_CALIBER": len(caliber_excluded),
            "POSSIBLE_MOVED": len(moved),
            "UNCHANGED": unchanged_n,
        },
        "bytes": {
            "path_added_recent_mtime": CN_tot.bytes,
            "path_added_backdated_mtime": PA_tot.bytes,
            "added_total": A_tot.bytes,
            "size_mtime_delta": sum(v[1] - v[0] for v in modified.values()),
            "path_removed_verified": R_tot.bytes,
            "path_gone_caliber": C_tot.bytes,
            "possible_moved": MV_tot.bytes,
            # INDEX ARITHMETIC ONLY -- see net_index_delta_caveat
            "net_index_delta": (A_tot.bytes - R_tot.bytes
                                + sum(v[1] - v[0] for v in modified.values())),
        },
        "net_index_delta_caveat":
            "Snapshot-index arithmetic over two enumerations. It is NOT a "
            "measurement of physical disk growth and must not be reported as "
            "one. Physical growth over the same window requires a matched "
            "volume-level used-bytes baseline; supply it via "
            "--volume-baseline to have this tool print the comparison.",
        "class_semantics": CLASS_META,
        "artifact_prefixes": list(artifacts_low),
        "added_recent_by_bucket": agg_d(CN_bucket),
        "added_recent_by_vol": agg_d(CN_vol),
        "added_recent_by_age_band": agg_d(CN_age),
        "added_backdated_by_bucket": agg_d(PA_bucket),
        "added_backdated_by_vol": agg_d(PA_vol),
        "added_by_bucket": agg_d(A_bucket),
        "added_by_vol": agg_d(A_vol),
        "added_by_age_band": agg_d(A_age),
        "modified_by_bucket": agg_d(M_bucket),
        "removed_by_bucket": agg_d(R_bucket),
        "possible_moved_by_bucket": agg_d(MV_bucket),
        "recently_touched_unchanged": young_unchanged.d(),
        "modified_in_window": modified_in_window.d(),
    }
    with open(base + "_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    # summary md
    def gib(n):
        return "%.2f" % (n / 2 ** 30)

    def top_table(aggs, n=15, extra_col=""):
        rows = sorted(aggs.items(), key=lambda kv: -kv[1].bytes)[:n]
        out = ["| bucket | files | GiB | %s |" % extra_col,
               "|---|---|---|---|"]
        for name, a in rows:
            exts = ",".join("%s:%.1fG" % (e, b / 2 ** 30) for e, b in
                            sorted(a.exts.items(), key=lambda kv: -kv[1])[:3])
            out.append("| %s | %d | %s | %s |" % (name, a.files, gib(a.bytes), exts))
        return "\n".join(out)

    with open(base + "_summary.md", "w", encoding="utf-8") as f:
        w = f.write
        w("# Snapshot diff %s (%s -> %s)\n\n" % (tag,
          time.strftime("%Y-%m-%d %H:%M", time.localtime(old_as_of)),
          time.strftime("%Y-%m-%d %H:%M", time.localtime(new_as_of))))
        w("- old: `%s` (%d entries)\n" % (os.path.basename(old_fn), len(old)))
        w("- new: `%s` (%d entries)\n" % (os.path.basename(new_fn), len(new)))
        w("- window: %.2f days\n" % summary["window_days"])
        w("- caliber artifact prefixes (REMOVED not counted as real): %s\n\n"
          % ", ".join("`%s`" % p for p in artifacts_low))
        w("## what each class proves / does not prove\n\n")
        w("| class | proves | does NOT prove | evidence basis |\n")
        w("|---|---|---|---|\n")
        for name, m in CLASS_META.items():
            w("| %s | %s | %s | %s |\n"
              % (name, m["proves"], m["does_not_prove"], m["evidence_basis"]))
        c = summary["counts"]
        w("\n## counts\n\n| class | entries |\n|---|---|\n")
        for k in ("PATH_ADDED_RECENT_MTIME", "PATH_ADDED_BACKDATED_MTIME",
                  "SIZE_MTIME_CHANGED", "PATH_REMOVED_VERIFIED",
                  "PATH_GONE_CALIBER", "POSSIBLE_MOVED", "UNCHANGED"):
            w("| %s | %d |\n" % (k, c[k]))
        b = summary["bytes"]
        w("\n## bytes\n\n| metric | GiB |\n|---|---|\n")
        for k in ("path_added_recent_mtime", "path_added_backdated_mtime", "added_total",
                  "size_mtime_delta", "path_removed_verified",
                  "path_gone_caliber", "possible_moved", "net_index_delta"):
            w("| %s | %s |\n" % (k, gib(b[k])))
        w("\n> `net_index_delta` is %s\n\n" % summary["net_index_delta_caveat"])
        w("\n## PATH_ADDED_RECENT_MTIME by bucket (top 15)\n\n")
        w(top_table(CN_bucket) + "\n")
        w("\n## PATH_ADDED_RECENT_MTIME by volume\n\n")
        w(top_table(CN_vol, 5) + "\n")
        w("\n## PATH_ADDED_BACKDATED_MTIME by bucket (top 15)\n\n")
        w(top_table(PA_bucket) + "\n")
        w("\n## PATH_ADDED_BACKDATED_MTIME by volume\n\n")
        w(top_table(PA_vol, 5) + "\n")
        w("\n## PATH_ADDED_RECENT_MTIME by mtime age band (vs new snapshot)\n\n")
        w("| band | files | GiB |\n|---|---|---|\n")
        for name in BAND_NAMES:
            a = CN_age.get(name)
            if a:
                w("| %s | %d | %s |\n" % (name, a.files, gib(a.bytes)))
        w("\n## SIZE_MTIME_CHANGED by bucket (top 15)\n\n")
        w(top_table(M_bucket) + "\n")
        w("\n## PATH_REMOVED_VERIFIED by bucket (top 15)\n\n")
        w(top_table(R_bucket) + "\n")
        w("\n## POSSIBLE_MOVED by bucket (top 15)\n\n")
        w(top_table(MV_bucket) + "\n")
        w("\n## not provably new, but mtime-recent\n\n")
        w("| class | files | GiB |\n|---|---|---|\n")
        w("| young_unchanged (<=7d at old snapshot, untouched since) | %d | %s |\n"
          % (young_unchanged.files, gib(young_unchanged.bytes)))
        w("| modified_in_window (mtime moved inside window) | %d | %s |\n"
          % (modified_in_window.files, gib(modified_in_window.bytes)))
        if vol_base_fn and os.path.exists(vol_base_fn):
            try:
                vb = json.load(open(vol_base_fn, encoding="utf-8"))
            except Exception as exc:
                vb = None
                w("\n## physical vs index\n\n(failed to read baseline: %s)\n" % exc)
            if vb:
                w("\n## physical vs index (volume-level baseline)\n\n")
                w("source: `%s`\n\n" % os.path.basename(vol_base_fn))
                w("| vol | used_mid GiB | enumerated GiB | residual GiB "
                  "| drift MiB | dir denials |\n|---|---|---|---|---|---|\n")
                for v in ("C", "D"):
                    d = vb["volumes"][v]
                    w("| %s: | %s | %s | %s | %s | %s |\n"
                      % (v, d["used_mid_GiB"], d["enumerated_GiB"],
                         d["residual_mid_GiB"], d["drift_MiB"],
                         d.get("dir_errors")))
                w("\nThe index delta above (%s GiB) and the volume-level "
                  "residual are different measurements of different things; "
                  "neither one is a substitute for the other.\n"
                  % gib(b["net_index_delta"]))

    print("wrote %s_summary.{json,md} + _detail.jsonl (%.0fs total)"
          % (base, time.time() - t0))
    print("counts: %s" % json.dumps(summary["counts"]))
    print("bytes: %s" % json.dumps({k: gib(v) for k, v in summary["bytes"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
