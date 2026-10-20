# File Organizer R3 read-only scan review | 2026-10-20

Source: N1 WorkBuddy email at 2026-10-20 00:13 +08, attachment `file-organizer-public-bundle-20261019-phase2.txt` (50 sanitized files). This page is an independent review of its reports and selected source code, not direct verification of local execution.

## Accepted improvements
- `tools/disk_scan.py` now treats `(st_dev, st_ino)==(0,0)` as **identity unavailable**, rather than a hard-link duplicate. This repairs missing `pagefile.sys` (16.287 GiB) and swapfile (0.016 GiB) enumeration. This specific code branch was inspected in the attachment.
- `tools/volume_bracket.py` measures volume-used before and after the scan. N1 reports C 152.637 GiB used, 107.535 GiB enumerated, residual 45.102 GiB; D 117.091 used, 116.333 enumerated, residual 0.758 GiB. The small observed drift makes the arithmetic more comparable.
- The prior lean-workstation 130–150 GiB estimate is explicitly withdrawn. A–F candidate groups total ~39.2 GiB, corresponding to an **illustrative** 269.728 − 39.2 ≈ 230.5 GiB remaining, if every such candidate were fully removed without regrowth. There is no observed real clean-install baseline.
- N1 reports phase-only scans, HOLD flags, and zero deletions during the new phase. Keep that as the operational boundary.

## Remaining precision defects and acceptance conditions
1. `tools/snapshot_diff.py:79-87,286-296`: `CONTENT_NEW_VERIFIED` is still overconfident. `(old index lacks path) AND (mtime >= old_as_of)` verifies a newly observed path with a recent modified timestamp; it **cannot verify new content**. This is also admitted by the code's `does_not_prove` text. Rename it to `PATH_ADDED_RECENT_MTIME` or equivalent; reserve `CONTENT_NEW_VERIFIED` for stronger content-identity or provenance evidence. Keep the 34.82 GiB number in a recent-mtime subgroup rather than labeling it verified newly created content.
2. The `net_index_delta` +34.08 GiB is index arithmetic only; never describe it as net physical growth. `POSSIBLE_MOVED` matched on size+mtime is a heuristic. These caveats are correctly recorded, but reports should consistently use them.
3. C remaining residual 45.102 GiB is still PARTIAL, not proven VSS or NTFS metadata. The asserted upper bound `<=3 GiB` for 501 denied directories is **an assumption**, not a defensible upper bound without inspecting sizes. The fact that 401 are WER crash archives may itself warrant category-level diagnosis after read-only elevated inspection. Record `UNKNOWN` or a qualified rough estimate, not a hard maximum.
4. `EVIDENCED_CLEANUP_CEILING = 39.2 GiB` is not an absolute ceiling: it is the sum of **currently identified conditional candidates**. `POST_CLEANUP_FLOOR = 230.5 GiB` is not an intrinsic floor: it is a single what-if subtotal if the selected candidate groups are entirely removable. Rename to `IDENTIFIED_CANDIDATE_TOTAL` and `POST_SELECTED_CANDIDATES_SCENARIO` to avoid false precision; account for app reinstallation and ongoing writes.
5. The local host is not running elevated (`IsAdmin=False`); N1 reports permission errors from VSS/NTFS/DISM. An authorized one-time **read-only** elevated collection is the next evidence-gathering hinge; no cleanup, deletion, or service configuration changes are implied.

## Current decision
Status: scan-analysis **PARTIAL PASS**, no deletion authorized. No new scheduled ChatGPT monitoring task. Keep existing snapshots and journals. Work only on narrowed evidence gaps and labeling; do not broaden architecture or assert cleanup targets achieved.

## Single NEXT
With proper operator authorization, run one elevated read-only C: space attribution pass and publish a sanitized allocation reconciliation; adjust two over-strong metric labels. Then evaluate whether the current scanning system meets its completeness and reproducibility criteria.