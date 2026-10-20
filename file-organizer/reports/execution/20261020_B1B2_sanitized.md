# B1/B2 execution evidence (sanitized)

- Batch: `SAFE_B1_B2`
- Executed: 2026-10-20 11:57 +08:00
- Volume window: 2026-10-20T11:57:06+08:00 -> 2026-10-20T11:57:42+08:00
- Method: official `pip cache purge`, `npm cache clean --force`, `pnpm store prune`; plus a purpose-built whitelist file purger (hardcoded roots, realpath containment, no reparse traversal, files only, 600 MiB per-file cap, 6 GiB batch cap, skip files modified within 10 minutes, per-file JSONL journal).
- C05 WER/CrashDumps excluded by instruction. Edge cache skipped: msedge/msedgewebview2 were running. No process was terminated.
- Browser profile, session, passwords, bookmarks, `.state`, logs/index ledger, Git worktrees, configs and credentials were outside the whitelist.

## Volume result (same-call Size/FreeSpace)

| Volume | Used before (bytes) | Used before (GiB) | Used after (bytes) | Used after (GiB) | Used change (GiB) | Free change (bytes) |
|---|---:|---:|---:|---:|---:|---:|
| C: | 171,863,289,856 | 160.061 | 168,590,561,280 | 157.012 | -3.048 | +3,272,728,576 |
| D: | 125,702,168,576 | 117.070 | 125,703,307,264 | 117.071 | +0.001 | -1,138,688 |

Net freed (used decrease): **C +3.048 GiB, D -0.001 GiB**.

D is effectively zero: `pnpm store prune` removed only 11 metadata files (147,001 B) because the remaining 15,849 files are referenced by projects; the store must not be force-deleted.

## Deleted items

| id | Item | Files | Bytes | GiB | Result |
|---|---|---:|---:|---:|---|
| C01 | Chrome Cache | 2,104 | 420,767,172 | 0.392 | DELETED |
| C01 | Chrome Code Cache | 5,030 | 299,581,669 | 0.279 | DELETED |
| C02 | pip cache (official purge) | 1,341 | 484,694,118 | 0.451 | DELETED |
| C04 | npm _cacache (official clean) | 66 | 6,646,705 | 0.006 | DELETED |
| C06 | AI client model download *.partial (abandoned) | 1 | 27,000,640 | 0.025 | DELETED |
| C07 | AI updater installer | 1 | 469,579,952 | 0.437 | DELETED |
| C08 | AI updater installer + pending | 5 | 707,498,104 | 0.659 | DELETED |
| C09 | AI updater installer + pending | 5 | 579,302,715 | 0.540 | DELETED |
| C10 | AI updater installer | 1 | 268,723,830 | 0.250 | DELETED |
| C03 | pnpm store prune (official) | 11 | 147,001 | 0.000 | DELETED |

Known deleted files: 8,565. Failed files: 0. Skipped files: 0.
Observed C free gain exceeds the known file bytes by about 8.9 MB, consistent with background system activity in the 36-second window; the accepted release figure is the observed same-volume delta.

## Regeneration evidence

- Chrome cache: browser-managed cache; profile databases remained present after the run (Login Data 40,960 B, Preferences 76,563 B). No profile/session files were in the whitelist.
- pip/npm: official commands; packages and HTTP bodies are re-downloadable. npm left `_logs` and `_npx` untouched.
- pnpm: official prune removed unreferenced metadata only; the referenced store remains usable.
- Model download: single interrupted `.partial`; the bundle's adopted marker file was kept.
- AI updaters: installers and pending artifacts are re-downloadable. Installed versions before the run: Cherry Studio 2.1.6 (pending 2.1.7), DeepSeek Harness 0.2.0-rc.2 (pending 0.2.1-rc.2). The pending updates had not been applied; both applications remain on their installed versions and will re-check/download on next launch. This is a deviation from the literal "upgrade completed" condition and is flagged for review.
- WER/CrashDumps were not touched.
