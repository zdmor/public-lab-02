# src/ — sanitized cleanup code (version 2026-10-20)

Public-safe copies of the actual code, produced by
`tools/build_public_bundle.py` (literal redaction + BANNED regex,
leaks=0 at build time). No credentials, user names, or raw file lists.

| file | role |
|---|---|
| `tools/disk_scan.py` | full-volume enumeration; `## coverage` table; per-path denial log; `(dev,ino)==(0,0)` no-longer treated as a hardlink (2026-10-20 fix) |
| `tools/volume_bracket.py` | bracketed same-time-point volume measurement (before/after enumeration, midpoint + drift) |
| `tools/snapshot_diff.py` | same-caliber two-snapshot diff; evidence-graded classes (`PATH_ADDED_RECENT_MTIME` / `PATH_ADDED_BACKDATED_MTIME` / `CONTENT_NEW_VERIFIED` reserved-empty / `SIZE_MTIME_CHANGED` / `PATH_REMOVED_VERIFIED` / `PATH_GONE_CALIBER` / `POSSIBLE_MOVED`) |
| `tools/housekeep_exec.py` | mutex + read-only phase gate (`--approve` refused rc=4 while the phase marker exists) |
| `tools/probe_inode_zero.py` | reproduction script for the pagefile accounting hole |
| `tools/backfill_manifests.py` | backfill snapshot manifests (schema_version/scan_id/as_of/manifest_hash) |
| `gov/*` | governance CLI (candidates / inventory / journal / policy / release) |

Read-only phase is enforced by `.state/PHASE_SCAN_BASELINE`; no code in this
tree deletes anything while that marker exists.
