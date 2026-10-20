# file-organizer run log — 2026-10-19 (anonymized)

Machine: DESKTOP-EXAMPLE-001 (Windows 10 Home, build 26200.x, OS install
2025-09-26). All numbers below were measured on the machine at the stated
times; nothing here is a plan presented as a result.

## 1. Volume baseline (2026-10-19T08:43:04Z)

| vol | total | used | free |
|---|---|---|---|
| C: | 198.97 GiB | 152.55 GiB | 46.41 GiB |
| D: | 257.05 GiB | 116.20 GiB | 140.85 GiB |

Same-time-point full scan (2026-10-19T09:37:21Z, read-only, reparse points not
descended, hardlinks counted once per inode):

| vol | files | logical bytes | reparse | hardlink hits | dir errors |
|---|---|---|---|---|---|
| C: | 692,071 | 91.73 GiB | 108 | 68,413 | 501 |
| D: | 485,707 | 115.81 GiB | 1,331 | 28,404 | 2 |

(The gap between "used" and scanned bytes on C: is permission-denied system
area plus once-counted component-store sharing.)

## 2. P0 code fixes (from the 2026-10-19 review)

- **release_exec.py v2 (fail-closed).** v1 skipped the hash check when
  `src_hash` was empty, never bound `trash_path` to the journal MOVE `dst`,
  and re-verified retention only as manifest age. v2 refuses on: missing
  hash, path/dst substitution, unexpired per-entry retention (measured from
  the MOVE event), undone or uncommitted events, reparse/symlink identity,
  paths outside the managed root, and hash change at delete time. It journals
  a planned event before every delete and a per-file result after. Negative
  suite: `tools/tests/test_release_exec_failclosed.py` — 12/12 pass.
- **housekeep_exec.py v2 (honest accounting).** v1 counted the whole original
  file as freed for a gzip action. v2 reports `raw_bytes_removed`,
  `gz_bytes_created`, `net_bytes_delta` and volume free before/after.
- **P0-2 staging audit** (`tools/desktop_staging_audit.py`): recomputes the
  D-side destination digest AND the C-side staging digest for every done
  triage MOVE. Result: 95 audited, 91 VERIFIED_PURGEABLE (1,236.2 MiB /
  1,266 files), 3 HELD (two D-side copies changed after the run, one D-side
  destination missing), 1 fixture skipped.

## 3. Purge channel (isolated from gov/, PRD I1 intact)

`tools/purge_engine.py`: positive whitelist only, frozen prefixes checked
first, budget (bytes/items/seconds) as hard caps, HOLD kill-switch, per-file
NDJSON journal, reparse never descended. Official tools preferred where they
exist (`npm cache clean --force`). Verified staging trees are re-hashed on
BOTH sides immediately before deletion.

Executed batches (2026-10-19, all `--approve`, C: volume):

| batch | rules | freed | files | failed | held |
|---|---|---|---|---|---|
| 1 | verified staging duplicates | 495.2 MiB | 391 | 0 | 89 |
| 2 | staging remainder + temp(>7d) + download fragments + old WER + user-ordered folder | 880.3 MiB | 3,945 | 6 | 92 |
| 3 | npm official cache clean | 610.1 MiB | 2,268 | 0 | 0 |
| **total** | | **1,985.6 MiB** | **6,604** | **6** | **181** |

- The 6 failures were OS-locked component files inside Temp (held by running
  browser processes); skipped, not forced.
- Batch 2's volume measurement is the clean one: C: free moved +0.84 GiB
  against 880.3 MiB (0.86 GiB) deleted. Other batches' in-window volume
  deltas were masked by concurrent disk writers on the machine (see §5).
- Nothing outside the whitelist was touched: no Windows directories, no
  Recycle Bins, no Program Files, no IM-client tree, no user documents.

## 4. Self-state housekeep (D:)

- Session run: 18 gzip actions, 1.402 GB raw removed, 75.9 MB .gz created,
  net 1.331 GB (journal/evidence never touched).
- Observed `.state` total: 9.2 GB (16:55) -> 2.6 GB (17:20). The session
  process can only prove 1.331 GB of that; the remainder coincides with an
  UN-ATTRIBUTED concurrent housekeep activity between 17:05 and 17:17
  (see §5).

## 5. Concurrency finding (flagged, not hidden)

Between 17:05 and 17:17 another process on the machine gzipped/deleted
`.state` snapshots with a more aggressive policy than the configured
180-day floor. Evidence: the eligible-plan count dropped 67 -> 18 with no
`.gz` files time-stamped in between, and ~14 `.gz` files (~190 MB) appeared
inside the purge window that this process did not create. No Task Scheduler
entry existed for this project at the time (verified: zero matching tasks),
so the actor could not be attributed — most plausibly a concurrent agent
session. Mitigation added: the purge engine and housekeep now run under the
scheduled tasks below; a mutex for housekeep is the recommended next fix.

## 6. Natural scheduled triggers (created + verified today)

| task | schedule | verification |
|---|---|---|
| FileOrganizer-DailyCycle | daily 06:30 | fired 2026-10-19 18:05:17, LastResult=0, KPI line written; housekeep net 396.7 MB matched measured volume delta +396.7 MB exactly |
| FileOrganizer-WeeklyReview | Mondays 08:00 | fired 2026-10-19 18:06:29, LastResult=0, weekly report regenerated |
| FileOrganizer-24h-Recheck | once, +25h | Ready, next run 2026-10-20 19:05 |

## 7. Error ledger

| id | event | disposition |
|---|---|---|
| E1 | 6 OS-locked Temp files (browser-held) | skipped, counted as failed, no retry |
| E2 | 3 staging entries held by audit (D-side drift / missing dst) | kept in C: staging, not purged |
| E3 | GitHub write blocked (see §8) | email fallback |
| E4 | concurrent un-attributed housekeep (§5) | flagged; lock recommended |
| E5 | in-window volume deltas masked by concurrent writers | KPI uses provable deleted bytes + quiet-window measurement |

## 8. GitHub status

Push attempts to the public repo failed with
`403 Resource not accessible by integration` on the git trees/contents
endpoints (read operations succeed). Exact blocker reported to the reviewer;
sanitized bundle provided via email instead.

## 9. NEXT

Drain the 181 held items in further budget-capped batches, then present the
approval-gated buckets (leftover app profile ~4.3 GB, Recycle Bins ~12.3 GB,
download folders ~6.8 GB, old sync archives ~15.7 GB, software uninstall
review) for one-time batch approval.

## 10. PHASE_SCAN_BASELINE (supersedes §9, decision received 2026-10-19T12:37:58Z)

The reviewer's phase decision froze all new cleanup batches, recycle-bin
emptying, uninstalls, permanent deletes and date-based batch deletes, and
ordered a read-only inventory phase. Executed:

- **Safety switches**: `.state/purge/HOLD` + `.state/desktop_triage/HOLD`
  kept (purge journal 1,879 lines fully retained); `FileOrganizer-DailyCycle`
  retargeted to `tools/daily_readonly.py` (next fire 2026-10-20 06:30);
  `.state/PHASE_SCAN_BASELINE` marker created.
- **housekeep_exec.py v3**: mutex lock (`.state/housekeep/LOCK`, pid+stamp,
  `O_EXCL`; live pid refuses rc=3, stale pid stolen) + read-only phase gate
  (`--approve` refused rc=4 while the phase marker exists). Tested all three
  gates: dry-run rc=2, phase refusal rc=4, mutex rc=3 (live pid 19484).
- **Snapshot manifests** (§5 of the decision): `gov/cli.py` now writes
  `<snapshot>.manifest.json` with `schema_version + scan_id + as_of +
  volume_coverage + manifest_hash` (+entry_count/total_bytes/roots); 9 existing
  snapshots backfilled via `tools/backfill_manifests.py`.
- **Same-caliber diff** (`tools/snapshot_diff.py`, reuses disk_scan's bucket
  logic): 10-11 18:04 -> 10-19 20:48 (8.11 days). VERIFIED_ADDED 151,638 /
  46.19 GiB (fresh mtime 34.82, backdated 11.37); MODIFIED 16,626 / net
  -1.71 GiB; REMOVED_real 32,603 / 10.40 GiB; REMOVED_caliber_excluded
  18,159 / 7.73 GiB (IM-client data tree frozen 10-19 — tagged, not counted
  as deletion); MOVED 12,768; UNCHANGED 450,181. Net **+34.08 GiB**,
  cross-checked against the snapshot-set trend (+34.07 GiB).
- **Volume reconciliation** (C: residual 61.46 GiB): pagefile 16.29 GiB
  PROVEN (WMI), hiberfil 0 PROVEN (hibernation disabled), swapfile 16 MiB;
  System Volume Information / VSS / NTFS metadata UNRECOVERABLE without
  elevation; ~45 GiB residual left explicitly PARTIAL — not treated as
  deletable space. D: residual 1.11 GiB, closed.
- **Read-only e2e**: `daily_readonly.py` rc=0 (21:14 local), KPI line
  `mode=READ_ONLY`, `purge_hold_present=true`; no writes outside `.state`.
- **Deliverables** (8 sanitized docs + code): baseline, volume-reconciliation,
  earliest-history-evidence, file-delta-summary, age-cohorts, app-space-top,
  cleanup-candidates (15-30 buckets, analysis only), read-only-e2e.
- GitHub write still 403 (read-only OK); sanitized bundle + docs delivered by
  email fallback in small segments (Gmail rejects .zip attachments).
- **NEXT**: reviewer's batch approval for any T1/T2/T3 or option 1-6; one
  elevated collection (vssadmin/fsutil/dism) to close the C: residual.

## 11. 2026-10-20 — 评审整改（SCAN_PHASE_REVIEW_20261019 P0 四项）

Reviewer (Ding Zhai, 2026-10-19T15:20:33Z) held the cleanup batches at HOLD and
required three P0 fixes plus two cross-checks. All executed read-only.

- **P0-1 classification semantics** (`tools/snapshot_diff.py`): the audit
  ruled that "path absent from the old index" verifies a newly observed path,
  not new content, and that `(size,mtime)` pairing is heuristic, not file
  identity. Labels renamed: `VERIFIED_ADDED` -> `CONTENT_NEW_VERIFIED`
  (mtime >= old_as_of) + `PATH_ADDED_VERIFIED` (backdated mtime, content age
  UNKNOWN); `MODIFIED` -> `SIZE_MTIME_CHANGED`; `REMOVED_real` ->
  `PATH_REMOVED_VERIFIED`; `REMOVED_CALIBER_EXCLUDED` -> `PATH_GONE_CALIBER`;
  `MOVED` -> `POSSIBLE_MOVED`. Every class now carries an explicit
  proves / does-not-prove / evidence-basis triple in the output.
  `net_delta` -> `net_index_delta` with a printed caveat that it is snapshot
  index arithmetic and NOT evidence of physical disk growth. Rerun
  (`20261019_235435_snapdiff_1011_to_1019_r2`): CONTENT_NEW_VERIFIED 109,950 /
  34.82 GiB; PATH_ADDED_VERIFIED 41,688 / 11.37 GiB; SIZE_MTIME_CHANGED 16,626
  / -1.71 GiB; PATH_REMOVED_VERIFIED 32,603 / 10.40 GiB; PATH_GONE_CALIBER
  18,159 / 7.73 GiB; POSSIBLE_MOVED 12,768 / 2.15 GiB; UNCHANGED 450,181.
- **P0-3 coverage metric** (`tools/disk_scan.py`): directory denials are now
  recorded per path with winerror into `<stamp>_dir_errors.jsonl`, and the
  summary carries a `## coverage` table. C: 245,162 dirs visited / 501 denials
  (2.0 per 1k), D: 73,984 / 2. All 503 are WinError 5. 401 of them are WER
  `ReportArchive\AppCrash_dwm.exe_*` dirs, 9 are Defender service dirs, the
  rest are Windows service/state dirs (sru, config, spool, Tasks, LogFiles,
  DriverState, SleepStudy, WDI). None is a GiB-scale consumer at default
  configuration -> upper bound estimate <~3 GiB (EXPERT_ESTIMATE, not
  measured), so the denials cannot explain the residual.
- **P0-4 bracketed measurement** (`tools/volume_bracket.py`, new): volume
  readings immediately before and after the enumeration, midpoint reported
  with drift. C: used_mid 152.637 GiB, enumerated 107.535 GiB, drift +0.18 MiB,
  **residual 45.102 GiB**. D: used_mid 117.091, enumerated 116.333, drift
  +0.73 MiB, **residual 0.758 GiB**.
- **Accounting bug found and fixed** (`tools/disk_scan.py::walk_volume`):
  `os.stat()` returns `st_dev=0, st_ino=0` for `C:\pagefile.sys`,
  `C:\swapfile.sys`, `C:\DumpStack.log.tmp`. The hardlink dedup treated (0,0)
  as "already seen" and silently dropped them — pagefile.sys alone is 16.287
  GiB, which then landed in the residual and was mis-attributed to
  unmeasurable system structures (i.e. the same bytes were missing from the
  enumeration and counted again as unexplained residual). Fix: (0,0) bypasses
  dedup and is counted normally, tracked as `no_identity`. Effect: C:
  enumerated 91.232 -> 107.535 GiB, residual 61.406 -> 45.102 GiB.
- **P0-5 single-count check**: pagefile counted once (`C:/pagefile.sys`
  files=1, hl=0, no_identity=1); D: has no pagefile/swapfile (no_identity=0);
  WinSxS 16.598 GiB with 15,570 hardlink hits and System32 5.16 GiB with
  18,870 hits — dedup is per-volume on (dev,ino), so each link is counted
  once. Caveat recorded: which bucket a hit lands in depends on traversal
  order, so only the volume total is dedup-invariant.
- **P0-2 bridge ledger** (`docs/bridge-ledger.md`, new): 269.728 GiB used
  minus A+B 3.5, C 6.7, D 18.5, E 8.0, F 2.5 = **230.5 GiB**. Reaching
  130-150 would need another 80-100 GiB with no evidenced source; even
  assuming 100% of the C: residual were reclaimable (physically impossible for
  NTFS metadata) the floor is 185.4 GiB. **`LEAN_WORKSTATION_ESTIMATE`
  130-150 GiB is WITHDRAWN**, replaced by `IDENTIFIED_CANDIDATE_TOTAL` 39.2 GiB,
  `POST_SELECTED_CANDIDATES_SCENARIO` 230.5 GiB, and `UNSUBSTANTIATED_TARGET`
  130-150 GiB (renames per R3 item 4: the first is a sum of currently
  identified conditional candidates, not a ceiling; the second is a single
  what-if subtotal, not an intrinsic floor).
- Elevation still unavailable (`IsAdmin=False`); refusals on record:
  vssadmin exit=2, fsutil error 5, dism error 740, Get-ComputerRestorePoint
  access denied, Win32_ShadowCopy CimException. `fsutil usn queryjournal C:`
  succeeds (128 MB cap) and rules the USN journal out as a major term.
- **NEXT**: nothing requested. No new architecture proposal, no cleanup
  approval request, no new scheduled task.
