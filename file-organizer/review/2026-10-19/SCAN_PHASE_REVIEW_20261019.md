# File Organizer | Phase scan+baseline audit | 2026-10-19 23:14 +08

## Status verified from N1 replies
Received N1 emails 2026-10-19 21:38 and 22:55 +08. They report a full C/D inventory, earliest-evidence reconstruction, and a read-only phase switch. N1's GitHub writes are still HTTP 403; their supporting sanitized 47-file bundle was sent as a .txt email attachment and **has not itself been committed to this public repository**. This file records independent review of the attachment, not proof of runtime on the user's PC.

### Reported measured points
- C: 152.55 GiB used vs enumerated files 91.09 GiB. Gap 61.46 GiB. Pagefile 16.29 GiB has evidence; remaining ~45.15 GiB unexplained (PARTIAL). Need elevated, read-only checks of shadow-storage and filesystem metadata.
- D: 116.98 GiB used vs enumerated files 115.87 GiB; gap 1.11 GiB, attributed by N1 to filesystem/system overhead but not independently proven at allocation precision.
- Earliest indexed inventory: 2026-09-22 (140.76 GiB), but with a distinct filter scope; NOT the original Windows install-state snapshot.
- One comparable snapshot pair: 2026-10-11 -> 2026-10-19. N1 reports 151,638 ADDED paths / 46.19 GiB and a scope-adjusted +34.08 GiB net in the *managed candidates snapshot*, not a proven +34.08 GiB growth of the whole disk.
- N1 reports purge and desktop-triage HOLD enabled, daily cycle switched to read-only, 9 historical snapshot manifests preserved, and 0 deletion after phase switch. Independent remote-host runtime verification is not available through current connected endpoint.

## P0 audit issues

1. `tools/snapshot_diff.py` calls a path absent from the old path-index but present in the new index `VERIFIED_ADDED`. This **verifies a newly observed path**, not necessarily newly created content: moves, copies, renames, unobserved older files, changed scan coverage or duplicate hardlinks can contribute. Its MOVED pairing uses only `(size,mtime)` and is heuristic rather than file identity or hash. Rename results should be `POSSIBLE_MOVED`, and added paths should be separated into `PATH_ADDED_VERIFIED` vs `CONTENT_NEW_VERIFIED` (latter requires stronger evidence). Do not use the +34.08 GiB figure as proof of physical disk growth without a matched volume-level baseline.
2. The `LEAN_WORKSTATION_ESTIMATE` of *130–150 GiB for C+D combined* does not reconcile to the reported C+D currently used 269.53 GiB and only ~39 GiB of itemized A–F candidates. Simple subtraction is ~230.5 GiB, not 130–150 GiB. Do not subtract the D/E recycle/migration buckets a second time—they are already part of A–F. Produce an allocation ledger listing which additional ~80–100 GiB can actually be removed to support 130–150, or withdraw/relabel the estimate as an unsupported target.
3. C: unexplained ~45.15 GiB is the largest immediate diagnostic blocker. Acquire authorized elevated **read-only** `vssadmin list shadowstorage`, `fsutil fsinfo ntfsinfo C:`, applicable allocation metadata, and `dism /Online /Cleanup-Image /AnalyzeComponentStore`, then reconcile without treating remainder as disposable bytes. C: 501 directory access denials must be reported in coverage metrics.
4. Keep timestamps and scan scopes explicit: the 2026-09-22 earliest snapshot excludes user documents and is not comparable without a coverage-aware diff. Win32 used captured 21:14 and enumeration captured 20:46 are not simultaneous; current 100–230 MB/min writes may affect residual estimates.
5. Maintain read-only freeze. No new T1/T2/T3 or recovery-bin purge is currently authorized. Stage outcome is an initial analysis deliverable, not complete inventory/clean-baseline certification.

## NEXT
Read-only reconciled C: storage breakdown + correct snapshot-diff class labels + reproducible lean-workstation estimate, all from N1's local machine; commit anonymized evidence to this existing repo or send a redacted email fallback for authorized review. No new ChatGPT periodic task is required.