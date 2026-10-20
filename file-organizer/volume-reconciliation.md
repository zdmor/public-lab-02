# C/D 卷占用对账（PHASE_SCAN_BASELINE，2026-10-20 修订）

> 裁决要求：解释 C 卷"已用−枚举"差额，**不得当作可删空间**，
> 证据不足处标 PARTIAL / UNRECOVERABLE / UNKNOWN。本文全部数字带采集时刻。
>
> **2026-10-20 修订**：上一版称 C: 残余 61.46 GiB。该数字包含了
> `C:\pagefile.sys` 16.29 GiB——它不是"残余"，而是一个被扫描漏计的真实文件
> （见 §4 修复说明）。修复后枚举总量 +16.30 GiB，**残余修正为 45.10 GiB**。
> 差额从"已用与枚举不同时采样"改为括号式同时点测量（drift ≤0.73 MiB）。

## 1. 对账公式与同时点测量

```
residual = used_mid − enumerated
used_mid = (used_before + used_after) / 2      # 枚举前后各取一次卷读数
```

采集：`tools/volume_bracket.py`，2026-10-19 23:58:38–00:01:02 本地（15:58–16:01Z）
产物：`.state/scan/20261019_235838_volume_bracket.json`

| 卷 | 总容量 | used_before | used_after | drift | used_mid | 枚举字节 | 文件数 | residual |
|---|---|---|---|---|---|---|---|---|
| C: | 198.94 GiB | 152.637 | 152.637 | +0.18 MiB | **152.637** | **107.535** GiB | 694,708 | **45.102 GiB** |
| D: | 257.05 GiB | 117.091 | 117.092 | +0.73 MiB | **117.091** | **116.333** GiB | 486,534 | **0.758 GiB** |

drift 0.18 / 0.73 MiB：扫描期间卷读数基本静止，used_mid 与枚举可视为同时点。
（上一版 61.46 GiB 用 21:14 的卷读数配 20:46 的枚举，机器当时并发写盘
100–230 MB/min，是不同时刻的数字——审计意见 P0-4 已指出，本版已消除。）

## 2. C: residual 45.102 GiB 归因表

| 成分 | 体量 | 证据等级 | 依据 |
|---|---|---|---|
| 页面文件 `C:\pagefile.sys` | 16.287 GiB | **PROVEN（已计入枚举，不在残余内）** | 修复后桶 `C:/pagefile.sys` files=1 / 16.287 GiB / hl=0 / no_identity=1；candidates 快照 20:48 同值；`AutomaticManagedPagefile=True` |
| `swapfile.sys` | 0.016 GiB | PROVEN（已计入枚举） | 同上，files=1 / 0.0156 GiB |
| `DumpStack.log.tmp` | ~0 GiB | PROVEN | 同类无身份文件，C: 共 3 个 |
| 休眠文件 `hiberfil.sys` | 0 | **PROVEN（不存在）** | `powercfg /a`："休眠尚未启用"；`Get-Item` ItemNotFound（缺失，非拒绝） |
| System Volume Information（VSS 影子副本 + 系统还原） | 未知 | **UNRECOVERABLE（未提权）** | `Get-ChildItem` UnauthorizedAccessException；`Win32_ShadowStorage`/`Win32_ShadowCopy` CimException；`vssadmin` exit=2 |
| NTFS 元数据（$MFT/$LogFile/$Bitmap/$Secure/$Extend） | 未知 | **UNRECOVERABLE（未提权）** | `fsutil` 错误 5 / `dism` 错误 740 |
| 503 个拒绝目录背后的空间 | **UNKNOWN** | **UNKNOWN（原"上界 ≲3 GiB"已撤回，见 §3.1）** | 未测量任何被拒目录体积，无法排除单个 GiB 级占用；401 个 WER 归档从未被打开过 |
| 簇尾碎片 / 压缩差异 / 已分配未入索引 | 未知 | UNKNOWN | 无提权无法拆分 |
| **未解释残余** | **45.102 GiB** | **PARTIAL** | 需一次提权采集才能拆分（§5） |

**结论（PARTIAL）**：C: 的 45.102 GiB 残余中，**没有任何一项已被证明可回收**。
被拒绝的 503 个目录体积 **UNKNOWN**（未测量，见 §3.1）；按路径身份它们**不太可能**
是 45 GiB 的主体，但这是可能性判断，不是上界证明。
因此残余主体**最可能**是 SVI 影子副本 + NTFS 元数据 + 已分配未入索引的保留区。
**该残余不是可删空间**：在提权拆分前，按"差额=垃圾"清理是无证据动作。
对照：`fsutil usn queryjournal C:` 无需提权可查，USN 上限 128 MB / 分配差 32 MB，
已排除 USN 日志为大头。

## 3. 覆盖率：扫描看得见与看不见的部分（审计 P0-3）

`tools/disk_scan.py` 现已把每次拒绝写成路径级记录（`<stamp>_dir_errors.jsonl`），
并在 summary 输出覆盖率表。

| 卷 | 访问目录数 | 测到文件数 | 目录拒绝 | 每千目录拒绝率 |
|---|---|---|---|---|
| C: | 245,162 | 694,708 | **501** | 2.0 |
| D: | 73,984 | 486,534 | **2** | 0.0 |

全部 503 条拒绝的 WinError 均为 **5（拒绝访问）**，无其他错误类型。
按 3 段前缀分布（完整清单见 `20261019_235838_dir_errors.jsonl`）：

| 前缀 | 拒绝数 | 身份 |
|---|---|---|
| `c:/programdata/microsoft/windows` | 412 | **401 个是 `WER\ReportArchive\AppCrash_dwm.exe_*`**（dwm.exe 反复崩溃的转储归档），其余为 WER 子目录 |
| `c:/programdata/microsoft/windows defender` | 11 | Definition Updates / Scans / Quarantine / Support / NIS |
| `c:/windows/system32` 与 `syswow64` | 27 | sru、config、spool、Tasks、LogFiles\WMI、DriverState、SleepStudy、WDI、Com\dmp 等 |
| 其余（WUModels、Temp、SystemTemp、ServiceProfiles、security、PLA、Dell、Packages 等） | 53 | 系统服务状态目录 |

### 3.1 为什么撤回"上界 ≲3 GiB"（R3 评审第 3 条）

原稿写过"401 个 WER 归档单个通常是 KB 到个位数 MB，据此给出上界 ≲3 GiB"。
**该上界不可辩护，已撤回为 UNKNOWN。** 理由：

1. **没有测量**。503 个被拒目录的体积一个都没测到，任何"上界"都是从
   "典型值 × 数量"推出来的，前提本身未经本机验证。
2. **不能排除 GiB 级单项**。WER 归档可能包含完整崩溃转储（`.dmp` 可到数 GB），
   401 个里只要有少数几个大的，量级就变；Defender `Definition Updates`
   也可能到数百 MB。
3. **身份判断 ≠ 体积判断**。"默认配置下通常不大"是统计印象，不是本机事实。

改为 **UNKNOWN**，只保留一条可能性表述：按路径身份，这 503 个目录**不太可能**
构成 45.102 GiB 的主体——但这是"不可能很大"的弱判断，**不是上界证明**，
不能用来把残余归因缩小。

**WER 归档本身值得提权后做类别级诊断**：401 个 `AppCrash_dwm.exe_*` 说明
`dwm.exe` 在反复崩溃，这是系统稳定性信号，不只是空间问题。已列为待办。

## 4. 修复：`(st_dev, st_ino) == (0, 0)` 导致的漏计（本轮发现的账本漏洞）

`disk_scan.py` 用 `(st_dev, st_ino)` 去重硬链接。实测
`os.stat()` 对 `C:\pagefile.sys` / `C:\swapfile.sys` / `C:\DumpStack.log.tmp`
返回 **dev=0, ino=0**（系统持有独占句柄，取不到文件身份）。
旧逻辑把它们当成"已见过的硬链接"直接 `continue`，于是：

- `pagefile.sys` 16.287 GiB 与 `swapfile.sys` 0.016 GiB **从枚举总量中静默消失**；
- 它们落进"残余"，被误归因成"不可测的系统结构"——这正是上一版
  "pagefile 16.29 PROVEN 属于残余"这一错误说法的来源（实质是重复解释：
  文件被漏计一次，又在残余里被算一次）。

修复：`(0,0)` 不再参与去重，按普通文件计数，并单独记 `no_identity`
（`tools/disk_scan.py` `walk_volume()`）。修复前后对比：

| 指标 | 修复前（23:47 扫描） | 修复后（23:58 扫描） | 差 |
|---|---|---|---|
| C: 枚举字节 | 91.232 GiB | **107.535 GiB** | +16.303 GiB |
| C: residual | 61.406 GiB | **45.102 GiB** | −16.303 GiB |
| `C:/pagefile.sys` 桶 | files=0, hl_hits=1 | files=1, 16.287 GiB, hl=0, no_identity=1 | 修正 |

## 5. 单次计量核对（审计 P0-5）

| 项 | 核对结果 |
|---|---|
| pagefile 是否重复计量 | **否**。`C:/pagefile.sys` files=1 / hl_hits=0 / no_identity=1，只计一次；修复前它是 files=0（漏计），不是重复计 |
| D: 是否有 pagefile/swapfile | **无**。D: `no_identity` 计数 = 0，两个桶均不存在 |
| WinSxS 硬链接是否重复计量 | **否**。去重按卷内 `(st_dev, st_ino)` 全局生效：WinSxS 桶内 15,570 条命中、System32 桶内 18,870 条命中，都是"另一条路径已计过"而跳过，卷内每个硬链只计一次。**注意**：命中落在哪个桶取决于遍历顺序，因此**只有卷级总量是去重不变量，桶间归因会随遍历顺序变化** |
| C:/D: 之间是否重复 | **否**。去重集合按卷独立（`walk_volume(..., set(), ...)`），跨卷硬链在 NTFS 上不存在 |

## 6. 关闭残余缺口所需的一次性提权采集（本阶段未执行，本机非管理员）

本机 `IsAdmin=False`（`DESKTOP-EXAMPLE-001\example`，无 SeBackupPrivilege）。已固化的拒绝证据：

| 命令 | 结果 |
|---|---|
| `vssadmin list shadowstorage` | exit=2 "你没有正确的权限" |
| `fsutil volume allocationreport C:` / `fsinfo ntfsinfo C:` | 错误 5 拒绝访问 |
| `dism /Online /Cleanup-Image /AnalyzeComponentStore` | 错误 740 需要提升 |
| `Get-ComputerRestorePoint` | 拒绝访问 |
| `Win32_ShadowStorage` / `Win32_ShadowCopy` | CimException |
| `fsutil usn queryjournal C:` | **成功**（最大 128 MB / 分配差 32 MB）——已排除该项为大头 |

需要补的命令：

```powershell
vssadmin list shadowstorage          # 每卷影子副本占用 -> 拆分 SVI
vssadmin list shadows                # 影子副本清单与日期
fsutil volume allocationreport C:    # 卷分配报告（含元数据）
fsutil fsinfo ntfsinfo C:            # $MFT 大小
dism /Online /Cleanup-Image /AnalyzeComponentStore   # WinSxS 真实可回收量
```

在拿到上述输出前，C: 残余 45.102 GiB 维持 PARTIAL 标注，不纳入任何清理桶。

## 7. D: residual 0.758 GiB

D: 卷对账**闭合**：117.091 = 116.333 枚举 + 0.758 元数据（占已用 0.65%），
无未解释大项，无需提权即可接受。
