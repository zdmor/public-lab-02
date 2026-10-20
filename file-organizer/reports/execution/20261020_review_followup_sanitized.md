# Review follow-up (sanitized) — 2026-10-20

PHASE: `CAPACITY_REDUCTION_PRODUCTION`
REPLY_TO: ACK #6284474472 (four evidence tasks)
BOUNDARY: read-only verification and executor/tests only. No B3–B9 deletion, no recycle-bin
emptying, no bulk uninstall, no unique-data move, no elevation, no reinstall, no new
Issue/repo/scheduled task. All product/path names below are sanitized.

Volume unit: GiB = 1024³ bytes. Disk readings are `GetDiskFreeSpaceExW` used = total − free.

---

## 0. What this round actually did

| # | Task | Status | Evidence |
|---|---|---|---|
| 1 | Publish sanitized `b1b2_limited_purge.py` + minimal tests + deviation fix | DONE | `src/tools/b1b2_limited_purge.py`, `src/tools/tests/test_b1b2_limited_purge.py`; **PASS 36 / FAIL 0** |
| 2 | Same-caliber reconciliation of the C: +5.041 GiB | DONE, remainder UNKNOWN | 00:01 and 16:21 same-scanner snapshots + 00:01–10:51 birth/mtime window scan |
| 3 | B3 installers / B4 recycle / B5 migration copies + occupancy-ranked uninstall list | DONE (read-only) | sections 3–6 |
| 4 | Report only verifiable commits/reports/tests/readings | DONE | this commit + current volume readings |

No file was deleted this round. B1/B2 execution from the prior round remains the only actual
deletion (8,565 files, C −3.048 GiB net).

---

## 1. Executor revision and fix evidence

### 1.1 Deviations found in the executed v1

1. **Empty-directory pruning could traverse a junction.** v1 used `os.walk(root)` and then
   `os.rmdir(cur)` without checking the reparse attribute; a junction inside a purge root
   could have its target's empty subdirectories removed.
2. **No pending-upgrade precondition.** Updater roots were deleted on the assumption that no
   upgrade was in progress; nothing verified that a staged pending version had already been
   installed.

### 1.2 Changes in the published revision

- `Root(root_id, path, only_suffix=None, requires_completed_upgrade=False)` — every root must
  declare whether it is an updater root.
- `prune_empty_dirs()` now calls `os.lstat()` on each directory and skips anything that is a
  reparse point or not a real directory before `os.rmdir()`.
- `safe_to_delete()` re-checks, immediately before each delete: lstat reparse attribute,
  regular-file type, `realpath` containment inside the root, and the per-file cap.
- `read_staged_version()` reads `pending/update-info.json` (`version`), otherwise parses a
  version from a `pending/*.exe` filename.
- `installed_version()` probes the uninstall registry (HKCU, HKLM, 64-bit and 32-bit views)
  with a product-name prefix and returns the highest `DisplayVersion`.
- `upgrade_gate()` refuses the whole root when:
  - staged version unknown → `STAGED_VERSION_UNKNOWN`
  - installed version unknown → `INSTALLED_VERSION_UNKNOWN`
  - installed < staged → `UPGRADE_NOT_INSTALLED`

### 1.3 Tests (standalone PASS/FAIL harness, no external deps)

```
PASS 36  FAIL 0
```

Coverage:

- normal delete and journaling;
- path containment: a file reached through a junction is refused (`SKIP_ESCAPE`);
- empty-directory pruning does not remove a junction or its target's directories;
- per-file cap and total cap (`SKIP_OVER_CAP`, `SKIP_TOTAL_CAP`);
- file held open by another handle → `FAILED`, other files continue;
- min-age skip for recently modified files;
- pending-upgrade gate: older installed version refused, refusal reason recorded, staged file
  survives, refused-root counter increments; equal version allowed; unknown installed version
  refused;
- version ordering incl. prerelease (`0.2.0-rc.2 < 0.2.1-rc.2`, release > same prerelease);
- `safe_to_delete()` refuses a reparse point.

### 1.4 Live dry-run (read-only)

Run at 2026-10-20T16:14:09+08:00 against the real `%LOCALAPPDATA%` roots after B1/B2:

- C01 browser cache / code cache: `OK`, 0 files matched (already purged in B1/B2);
- C06 abandoned model partial: `OK`, 0 files;
- C07–C10 updater roots: `REFUSED_PENDING_UPGRADE`, reason `STAGED_VERSION_UNKNOWN`,
  `gate_refused_roots = 4`, 0 deleted;
- volume before = volume after, file journal empty.

The live run confirms fail-closed behavior: when a pending version cannot be proven installed,
the executor refuses the root instead of deleting it.

---

## 2. C: +5.041 GiB reconciliation

### 2.1 Same-caliber snapshots

Both rows use the same scanner (`src/tools/disk_scan.py`), same exclusions, same bucket rules.

| Metric | 2026-10-20 00:01 | 2026-10-20 16:21 | Δ |
|---|---:|---:|---:|
| C: used | 152.637 | 165.687 | **+13.050** |
| C: enumerated | 107.535 | 116.140 | **+8.605** |
| C: unexplained residual | 45.102 | 49.547 | **+4.445** |
| D: used | 117.091 | 117.804 | +0.713 |
| pagefile | 16.287 | 19.313 | +3.026 |

Enumerated bucket positives (C:): Arduino15 +8.269, pagefile +3.026, `$Recycle.Bin` +1.059,
Qoder app data +0.140, Temp +0.134, Documents +0.101, `.state` +0.086, `.qoder` +0.070,
Arduino cache +0.058, Panther +0.049, Kingsoft +0.045, Local\Microsoft +0.026.

Enumerated bucket negatives (C:): Claude-3p −0.698, Google −0.671, cherry updater −0.659,
deepseek updater −0.540, pip −0.451, raccoon updater −0.437, C-Link −0.359, Kingsoft roaming
−0.348, Packages −0.330, nanomuse updater −0.250.

Net = +12.977 − 4.384 = **+8.593 GiB**, consistent with the enumerated total delta.

### 2.2 The 10:51 window specifically

At 10:51 C: used was 159.697; pagefile was 18.306. Therefore:

```
+7.060 (volume) − +2.019 (pagefile) = +5.041 GiB "other" growth
```

No file inventory exists at 10:51, so a true path diff cannot be reconstructed retroactively.
A read-only scan now (811,344 files, 501 stat errors) aggregated every surviving file whose
**birth time or mtime** falls inside 2026-10-19 23:58:38 → 2026-10-20 10:51:00:

| Measure | GiB |
|---|---:|
| created in window | 1.808 |
| modified in window | 1.290 |
| created **or** modified (deduplicated) | **1.984** |
| upper bound of the 5.041 explained | 39.4% |
| **not file-visible (≥)** | **3.057** |

Largest surviving window buckets: browser application update 0.486, updater `crx_cache` 0.485,
AI client cache 0.274, Local\Microsoft 0.150, Temp 0.134, IM chat data 0.106, Qoder state 0.078.

This is an **upper bound**, not a net attribution: files deleted after the window are missing
from the scan, and modified files are counted at full size. Even so, at least **3.057 GiB** of
the 5.041 GiB cannot be explained by surviving file paths.

### 2.3 Conclusion

- The 16 h 20 m growth **is** mostly enumerated (8.605 of 13.050 GiB); the largest single cause
  is the ESP32 toolchain download (+8.269 GiB, 59,777 files, 13:27–14:40).
- The morning +5.041 GiB "other" is **not** explained by any surviving file path in that
  window; ≥3.057 GiB remains **UNKNOWN**. Candidates that cannot be measured without elevation:
  VSS shadow storage, NTFS metadata/allocation, 501 denied directories (401 WER-related), and
  files deleted inside the window.
- The residual also grew +4.445 GiB over the 16 h; it is still treated as UNKNOWN and is **not**
  counted as reclaimable space.
- No elevated collection was run: `vssadmin`/`DISM`/denied-directory measurement still requires
  operator authorization.

---

## 3. B3 — download-area installers (read-only itemization)

Totals: `D:\Downloads` 18 files / **2.607 GiB**; network-disk download staging 115 files /
**4.119 GiB**; combined **133 files / 6.726 GiB**.

| Group | Files | GiB | Verdict |
|---|---:|---:|---|
| Installer already installed at the same version | 4 | 1.334 | `INSTALLED_REDUNDANT` (re-downloadable) |
| Staged newer installer, installed version older | 1 | 0.464 | `PENDING_UPGRADE` — **keep** (5.8.4 staged vs 5.7.6 installed) |
| Vendor installers not matched to an installed product | 12 | 3.285 | `REACQUIRABLE_OR_UNKNOWN` — vendor download available, but no proof the product is unused |
| Incomplete downloads (`.crdownload`) | 2 | 0.067 | `INCOMPLETE_DOWNLOAD` — re-downloadable |
| User documents | 9 | 0.054 | `USER_DOCUMENT` — **keep** |
| Tool/course archives and module zips | 37 | 1.461 | `ARCHIVE_TOOL_PACKAGE` — user content, may be unique |
| Other scripts/config/logs | 67 | 0.060 | `OTHER` — mixed, not itemized as deletable |

Byte-identical duplicates inside the set (SHA-256 verified): two `en.stm32cubef1.zip`
(0.107 GiB each), two `en.stm32cubef1-v1-8-6.zip` (0.052 each), two `ARMCompiler506_b960.msi`
(0.080 each), plus two tiny text/setup files. Redundant duplicate bytes = **0.239 GiB**.

Notes:

- The staged WorkBuddy 5.8.4 installer is **newer** than the installed 5.7.6. Deleting it would
  discard a staged upgrade; the executor's new gate would refuse this condition. Keep.
- MDK536 (5.36) is older than the installed Keil 5.43; mdk543a matches the installed version.
  Both are re-acquirable vendor installers, but neither is proven unnecessary.
- The 37 module/course archives and the 9 user documents are **not** re-acquirable. They stay.

**B3 conclusion:** only 1.334 GiB (installers matching an installed version) plus 0.239 GiB of
byte-identical duplicates are defensible release candidates, and only after per-item
authorization. No B3 deletion was performed.

---

## 4. B4 — recycle bin (read-only verification)

Metadata parse: **6,991 items / 15.708 GiB** payload (C 6.602, D 9.106); 1,429 are directories.

| Original file still exists? | Items | GiB |
|---|---:|---:|
| yes | 309 | 2.536 |
| **no (recycle bin is the only copy)** | **6,682** | **13.171** |

Same-caliber on-disk check: C `$Recycle.Bin` 5.378 GiB / 24,744 files (2 dir errors),
D `$RECYCLE.BIN` 9.108 GiB / 4,320 files. The C metadata total is 1.224 GiB higher than the
scanner total; the difference is unresolved (directory payload vs scanner exclusions) and must
be reconciled before any emptying.

**Unique-copy risk:** 817 missing-original documents total **0.45 GiB** (PDF/Office/CAD/text).
They were deleted on 2026-10-19 from a desktop staging area into the recycle bin and include
submission/regulatory material that cannot be regenerated. This alone blocks emptying.

Also measured, not deleted:

- desktop staging area: 810 files / 0.754 GiB (30-day retention window);
- old migration trash: 4.34 GiB / 34,297 files, including one 1.88 GiB archive and a 0.59 GiB
  installer; no recovery path other than this copy.

**B4 conclusion:** `REMOVE_WITH_ACCEPTED_LOSS` is **not** supportable while 13.171 GiB has no
surviving original and 817 documents are recycle-bin-only. Recommended: restore/verify the 817
documents first, then re-measure. No emptying performed.

---

## 5. B5 — migration copies (dependency verification)

11 C: junctions point into the D: migration copies. The saved reparse scan was re-parsed and
each target was tested read-only:

| C: junction | D: target | Target exists |
|---|---|---|
| `%USERPROFILE%\.cache` | `D:\DiskMigration\C-Link\.cache` | **live** |
| `%APPDATA%\npm` | `D:\DiskMigration\C-Link\AppData-Roaming-npm` | **live** |
| `%APPDATA%\Tencent` | `D:\DiskMigration\C-Link\AppData-Roaming-Tencent` | **live** |
| `%LOCALAPPDATA%\OpenAI` | `D:\DiskMigration\C-Drive\AppData-Local-OpenAI` | **live** |
| `%USERPROFILE%\.sankuai` | `D:\DiskMigration\C-Link\.sankuai` | broken |
| `%USERPROFILE%\gbrain` | `D:\DiskMigration\C-Link\gbrain` | broken |
| `%APPDATA%\CatPawAI` | `D:\DiskMigration\C-Link\AppData-Roaming-CatPawAI` | broken |
| `%APPDATA%\TRAE SOLO CN` | `D:\DiskMigration\C-Link\AppData-Roaming-TRAE` | broken |
| `%LOCALAPPDATA%\ima.copilot` | `D:\DiskMigration\C-Drive\AppData-Local-ima.copilot` | broken |
| `%LOCALAPPDATA%\WTOS` | `D:\DiskMigration\C-Drive\AppData-Local-WTOS` | broken |
| `%LOCALAPPDATA%\Programs\Python` | `D:\DiskMigration\C-Drive\AppData-Local-Programs-Python` | broken |

Footprint (same-caliber, 16:21): `C-Link` 6.877 GiB and `C-Drive` 0.731 GiB on D, i.e.
**7.608 GiB** (the plan's 00:01 rows D06+D07 were 7.237 + 0.730 = 7.967 GiB; C-Link shrank
0.359 GiB after B1/B2).

The four live junctions mean D06/D07 are **not** a clean removable copy: deleting the D: target
would immediately break live C: paths. The seven broken junctions are already dangling; one of
them is the Python install junction (which is why `python` is not on PATH on this host).

**B5 conclusion:** reclassify D06/D07 from `REMOVE_CLEAN` to `HOLD / DEPENDENCY_UNVERIFIED`.
Any future action must first resolve each junction (repair or remove with authorization), then
re-measure. No junction and no migration copy was modified.

---

## 6. Occupancy-ranked applications, AI runtimes and models

Sizes are the same-caliber scanner's **deduplicated** bytes (a file reached twice through
hardlinks is counted once; `hardlink references` are shown separately). D: hardlink overlap is
only 0.5 GiB in total, so D app directory walks are effectively unique. "Release candidate" is
**conditional** and not authorized; it is not a promise of net free space.

### 6.1 C: largest measured items

| Item | Unique GiB | Hardlinked refs GiB | Role / impact if removed | Recovery | Recommendation |
|---|---:|---:|---|---|---|
| pagefile | 19.313 | 0 | System-managed paging | Auto-recreated | **KEEP** (system) |
| WinSxS component store | 16.598 | 1.619 | Windows components | `DISM` servicing | **KEEP**; reclaimable amount still UNKNOWN (needs elevation) |
| Arduino15 (ESP32 core 6.324, staging downloads 1.865) | 8.269 | 0 | Offline ESP32/Arduino builds | Re-download from Arduino | **ARCHIVE/REMOVE candidate** after confirming sketches; staging is the safest subset |
| Documents | 7.873 | 0.590 | User data | Backup only | **KEEP** |
| `C:\new_tdx64` (trading terminal) | 6.520 | 0 | Quant/trading workflow | Reinstall + configuration/login | **KEEP** |
| `$Recycle.Bin` | 5.378 | 0 | B4 item | — | **DO NOT EMPTY** (section 4) |
| Kingsoft local 2.876 + roaming 2.421 | 5.297 | 0 | Office suite configuration/data | Reinstall + config export | **KEEP** while office suite is in use |
| Windows\System32 | 5.174 | 3.891 | OS | — | **KEEP** |
| Qianwen local data 3.227 + app install on D 1.595 | 4.822 | 0 | AI client runtime and user data | Reinstall + re-login; local data may be unique | **KEEP** unless the client is retired; export data first |
| Local\Microsoft (app data) | 4.645 | 0 | Mixed Office/Edge/app data | Depends on sub-item | Itemize before any action |
| Program Files (x86)\Microsoft (Edge/WebView2/Copilot) | 2.540 | 8.358 | Browser + WebView2 + AI assistant; most files are hardlinked to other Windows components | Reinstall from vendor; WebView-dependent apps break meanwhile | **KEEP**; uninstalling may free only the unique 2.54 GiB while the 8.36 GiB remains linked elsewhere |
| Windows\Panther (install logs, X01) | 2.527 | 0 | Update diagnostics | Not regenerable | `REMOVE_CLEAN` candidate only with separate authorization + export |
| `.workbuddy_bak` (old agent runtime backup, C13) | 2.464 | 0.243 | Rollback point for the agent runtime | Lost rollback; reinstall old version may not reproduce | B6 candidate, 14-day health precondition |
| `$WinREAgent` | 1.957 | 0 | Recovery environment | Keep | **KEEP** |
| Packages (UWP) | 1.802 | 0 | Store/system apps | Per-app reinstall | Per-app itemization; do not delete the group |
| Remaining updater caches (5 dirs) | 1.166 | 0 | Staged installers for apps in use | Vendor re-download | Keep; B1/B2 already removed the abandoned ones |
| desktop staging area | 0.754 | 0 | 30-day triage archive | Undo window only | B4 item; **do not empty** |
| ms-playwright browsers | 0.689 | 0 | Browser automation runtime | Re-download | Candidate if automation is paused |

The raw (non-deduplicated) walk of `Program Files (x86)\Microsoft` is 10.898 GiB because Edge,
WebView2 and Copilot share hardlinked payloads; the same-caliber unique footprint is 2.540 GiB.
Any release estimate based on the raw walk would overstate by 8.358 GiB — this is exactly the
kind of overstatement this round is meant to eliminate.

### 6.2 D: largest measured items

| Item | GiB | Role | Recommendation |
|---|---:|---|---|
| Cloud-sync local mirror | 19.307 | Cloud-synced data; cloud may be authoritative | **KEEP** under M1; M2 (remove local mirror) only with operator confirmation that cloud is authoritative and versioned |
| Agent workspace | 11.126 | Current agent runtime/state | **KEEP** |
| Enterprise IM data tree | 7.908 | Frozen by management instruction | **KEEP / frozen** |
| Migration copies (C-Link 6.877 + C-Drive 0.731) | 7.608 | Live junction targets | **HOLD** (see section 5) |
| Old migration trash | 4.340 | No other copy | B4 item; do not delete without authorization |
| `Keil_v5` | 3.049 | Embedded toolchain | Keep (B3 installers are redundant, not the install) |
| User project/data areas (Projects 3.612 + Data 1.768 + AI_Labs 1.119) | 6.499 | User project and agent experiment data | Keep |
| iFLYBUDS Kit | 2.782 | Hardware companion app | Candidate if the device is no longer used |
| Qoder | 2.158 | Active IDE | Keep |
| WXWork install | 2.045 | Enterprise IM | Keep (data frozen) |
| AI apps: QianwenApp 1.595, WorkBuddyAI 1.015, Cherry 0.956, WorkBuddy 0.948, raccoon 0.895, Deepseek Harness 0.745, ima 0.717, Cursor 0.750, Reasonix 0.530 | 8.151 | AI clients; several overlap in function | Per-app retirement review; uninstall via vendor only |

### 6.3 AI models

Only three files larger than 50 MiB matched model/weight extensions across the measured model
roots (user profile, `D:\AI_Labs`, `D:\DiskMigration`, `D:\Program Files`, `D:\BookKnowledge`,
`D:\Projects`, `D:\Data`), and none is a local LLM weight:

| File class | GiB |
|---|---:|
| IM runtime asset (`.bin`) | 0.403 |
| Speech-to-text ONNX (int8) | 0.223 |
| IM media runtime (`.bin`) | 0.062 |

There is **no large local model store** to reclaim. AI footprint is dominated by application
runtimes and user data, not model weights; "delete AI models" is not a meaningful capacity lever
on this host.

### 6.4 Conditional release candidates (not authorized)

| Volume | Candidate | GiB | Condition |
|---|---|---:|---|
| C | Arduino15 staging downloads | 1.865 | No offline rebuild requirement; safe to re-download |
| C | Arduino15 ESP32 core | 6.324 | Sketches do not need the installed core; re-download accepted |
| C | ms-playwright browsers | 0.689 | Browser automation not in use |
| D | B3 installers matching installed versions | 1.334 | Per-item authorization; re-download available |
| D | B3 byte-identical duplicates | 0.239 | SHA-256 proof already recorded |
| D | Cloud-sync local mirror | 19.307 | Cloud is authoritative and versioned (M2), with explicit owner sign-off |

These are **conditions**, not approvals. B4 and B5 items are deliberately excluded because their
unique-copy risk is not resolved.

---

## 7. Current readings and KPI position

At **2026-10-20T16:21:23+08:00**:

| Volume | Total | Used | Free |
|---|---:|---:|---:|
| C: | 198.969 | **165.687** | 33.281 |
| D: | 257.054 | **117.804** | 139.250 |

KPI targets (used): C ≤ 106.46, D ≤ 87.63.

| Volume | Current | Required reduction | Remaining |
|---|---:|---:|---:|
| C: | 165.687 | −59.227 | 35.7% of current used |
| D: | 117.804 | −30.174 | 25.6% of current used |

B1/B2 realized C −3.048 GiB / D −0.001 GiB net (8,565 files, 0 failures). This round realized 0
bytes by design: it is verification and code only.

---

## 8. Unknowns and what an authorization would unlock

Still UNKNOWN and **not** counted as reclaimable:

- 3.057+ GiB of the morning +5.041 growth (no 10:51 inventory; likely VSS/NTFS/denied paths);
- C: residual 49.547 GiB (501 denied directories, NTFS metadata, VSS, allocation);
- WinSxS reclaimable amount (DISM needs elevation; error 740 previously);
- VSS shadow storage (vssadmin denied);
- D: root directories not individually measured.

An operator authorization for **elevated read-only collection only** (VSS list, DISM analyze,
denied-directory enumeration) would let the next round convert part of the UNKNOWN into
measured paths. No deletion authority is requested here.

---

## 9. Reproduction

- Executor: `python src/tools/b1b2_limited_purge.py --dry-run`
- Tests: `python src/tools/tests/test_b1b2_limited_purge.py` → `PASS 36 FAIL 0`
- Volume readings: `GetDiskFreeSpaceExW` total/free, used = total − free.
- Same-caliber scans: `src/tools/disk_scan.py`; bucket diff only within the same scanner.
- Raw private detail (paths, $I metadata, hashes) stays local under `.state/recon/` and is not
  published. This report contains no usernames, hostnames, credentials, or personal filenames.
