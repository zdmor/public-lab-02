# CURRENT PHASE: SCAN / BASELINE / DIFF — NO NEW PURGE

**2026-10-19 user decision supersedes the earlier cleanup-first plan.** First complete a reliable read-only inventory of C: and D:, reconcile volume-used against file enumeration, identify the earliest provable computer/file state, build clean-OS and lean-workstation estimates, distinguish verified-new files from inferred-recent files, and rank cleanup candidates with deletion impacts. **Do not initiate T1/T2/T3 or other new deletion batches, empty recycle bins, or run automatic state-snapshot purges until the user approves a reviewed batch. Preserve existing scan evidence and journals.**

Latest controlling decision (Chinese): [PHASE_SCAN_BASELINE_DECISION.md](./PHASE_SCAN_BASELINE_DECISION.md).

Earlier target of C used-space reduction 33.3% and D used-space reduction 25% is a future planning challenge only, not immediate execution authorization.

---
# File Organizer | N1–ChatGPT Cleanup Review | 2026-10-19

Goal: make the Windows PC materially leaner. This is a subfolder of the EXISTING PUBLIC repo; do not create any new repository.

## Quantified targets
- C: remove at least one third of its measured USED space, with C first.
- D: remove at least one quarter of its measured USED space.
- Baseline is the same-time measured C/D used_bytes, not disk total size. These are stretch targets, not permission to misreport deletion or break installed Windows.
- KPI: removed bytes + before/after C and D available bytes + 24-hour rebound. A same-volume MOVE is not reclaimed capacity.

## Deliverables from N1
1. baseline.md: volume capacity/used/free, Windows/essential apps clean-state estimate, state if no actual factory snapshot exists.
2. inventory-summary.md: anonymized top 30 folders/app categories, AI agents/models/logs/cache, old installers, duplicate environments, install dates and age distributions; avoid double-counting hardlinks.
3. cleanup-plan.md: ranked removable buckets by expected net GiB, C or D, age, current use, deletion/official uninstall impact and recovery, with one-shot batch approval choices and targets/shortfalls.
4. src/: sanitized copies of actual cleanup code and policy with version.
5. runs/YYYYMMDD.md: real execution, true permanent removed bytes, before/after free space, errors, next-day growth, and evidence hash.
6. decisions.md: ChatGPT code review, batch authorization from user, fixes, acceptance outcomes.

## Public repo security
Do not publish original file lists or machine configuration, names/paths that identify users or clients, credentials, private keys, tokens, recordings, messages, business data, or raw logs. Review staged diff and run secret scans before commit. Use anonymized classes/totals and sanitized code. If N1 lacks GitHub push permission, send only small redacted text via email; ChatGPT's authorized GitHub account can commit it here.

## Sequence
Real C+D scan and clean baseline -> commit public-safe inventory and plan -> ChatGPT review -> user approves high-impact buckets once -> N1 truly deletes/uninstalls -> verify volume free bytes -> commit reports and continue. Low-risk previously authorized regenerable cache may be cleaned with verification before batch approval. Prioritize C, then D.