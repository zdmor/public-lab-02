"""P0-2: audit the desktop-triage C-side staging against its D-side copies.

The autonomous triage copies a Desktop cluster to D:\\Data..., verifies the
copy with a per-file SHA-256 tree digest, then parks the C: original in
``%LOCALAPPDATA%\\desktop-triage-trash``. The original therefore still occupies
C: -- the "organized 1,279.9 MB into D:" line in the ledger freed nothing on
C:. This script closes that gap read-only first:

  for every done MOVE in every real run manifest
    - recompute the D: destination tree digest  (must equal the recorded one)
    - recompute the C: staging tree digest      (must equal the same digest)
    - refuse anything containing a reparse point
  -> VERIFIED_PURGEABLE   both sides present, byte-identical, no reparse
  -> MISMATCH_HOLD        digest mismatch / unreadable / reparse inside
  -> MISSING              either side gone

Only VERIFIED_PURGEABLE entries may later be purged, and only with a second
confirmation at delete time. Fixture/test runs (paths containing .fixture)
are skipped: they are not user data.

Usage:
    python tools/desktop_staging_audit.py [--json PATH]
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

import tomllib  # noqa: E402

import desktop_triage_apply as dta  # noqa: E402

STATE = os.path.join(REPO, ".state", "desktop_triage")
CONFIG = os.path.join(REPO, "config", "desktop_triage.toml")


def tree_digest_or_reason(root: str) -> tuple:
    """(digest, files, bytes) or (None, reason). Files hash directly;
    directories use the recursive tree manifest (same convention as the
    executor's undo path)."""
    try:
        if os.path.isdir(root):
            rows = dta.tree_manifest(root)
        else:
            rows = [(os.path.basename(root), os.path.getsize(root),
                     dta.sha256_file(root))]
        return dta.digest(rows), len(rows), sum(r[1] for r in rows)
    except dta.ApplyError as exc:
        return None, "reparse: %s" % exc
    except OSError as exc:
        return None, "unreadable: %s" % exc


def audit_one(rec: dict) -> dict:
    out = dict(rec)
    dst, trash = rec["dst"], rec["trash"]
    if ".fixture" in dst.casefold() or ".fixture" in trash.casefold():
        out["status"] = "SKIPPED_FIXTURE"
        return out
    if not os.path.lexists(dst):
        out["status"] = "MISSING_DST"
        return out
    if not os.path.lexists(trash):
        out["status"] = "MISSING_TRASH"
        return out
    d_dig, d_files, d_bytes = tree_digest_or_reason(dst)
    if d_dig is None:
        out["status"] = "MISMATCH_HOLD"
        out["reason"] = "dst %s" % d_files
        return out
    t_dig, t_files, t_bytes = tree_digest_or_reason(trash)
    if t_dig is None:
        out["status"] = "MISMATCH_HOLD"
        out["reason"] = "trash %s" % t_files
        return out
    out["dst_digest"] = d_dig
    out["trash_digest"] = t_dig
    out["staging_files"] = t_files
    out["staging_bytes"] = t_bytes
    if d_dig == t_dig == rec.get("tree_sha256"):
        out["status"] = "VERIFIED_PURGEABLE"
    else:
        out["status"] = "MISMATCH_HOLD"
        out["reason"] = "digest mismatch (dst=%s trash=%s recorded=%s)" % (
            d_dig[:12], t_dig[:12], str(rec.get("tree_sha256"))[:12])
    return out


def main() -> int:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_out = ""
    for i, a in enumerate(sys.argv[1:]):
        if a == "--json" and i + 2 <= len(sys.argv):
            json_out = sys.argv[i + 2]
    with open(CONFIG, "rb") as f:
        cfg = tomllib.load(f)

    records = []
    for mpath in sorted(glob.glob(os.path.join(STATE, "runs", "*", "manifest.json"))):
        with open(mpath, encoding="utf-8") as f:
            man = json.load(f)
        for r in man.get("results", []):
            if r.get("state") != "done":
                continue
            records.append({"run_id": man["run_id"], "rel": r.get("rel", ""),
                            "dst": r.get("dst", ""), "trash": r.get("trash", ""),
                            "tree_sha256": r.get("tree_sha256", ""),
                            "files": r.get("files", 0), "bytes": r.get("bytes", 0)})

    audited = [audit_one(r) for r in records]
    verified = [a for a in audited if a["status"] == "VERIFIED_PURGEABLE"]
    held = [a for a in audited if a["status"] in ("MISMATCH_HOLD", "MISSING_DST",
                                                  "MISSING_TRASH")]
    skipped = [a for a in audited if a["status"] == "SKIPPED_FIXTURE"]

    v_bytes = sum(a.get("staging_bytes", 0) for a in verified)
    print("audited=%d verified_purgeable=%d (%s, %d files) held=%d skipped_fixture=%d"
          % (len(audited), len(verified), dta_size(v_bytes),
             sum(a.get("staging_files", 0) for a in verified),
             len(held), len(skipped)))
    for a in held:
        print("  HOLD %s :: %s (%s)" % (a["rel"], a.get("reason", a["status"]),
                                        a["run_id"]))
    summary = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "trash_root": cfg["auto"]["trash_root"],
               "audited": len(audited), "verified": len(verified),
               "verified_bytes": v_bytes,
               "verified_files": sum(a.get("staging_files", 0) for a in verified),
               "held": held, "skipped_fixture": len(skipped),
               "items": audited}
    path = json_out or os.path.join(STATE, "audit_%s.json" % stamp)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("audit written: %s" % path)
    return 0 if not held else 1


def dta_size(n: int) -> str:
    return "%.1f MiB" % (n / 1048576.0)


if __name__ == "__main__":
    sys.exit(main())
