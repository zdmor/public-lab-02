# 扫描完整性对账（Scan Completeness Reconciliation）

> 阶段：`INITIAL_STATE_RECONSTRUCTION`（P0）
> 决策来源：`zdmor/public-lab-02` Issue #2 评论 `6281781711`（2026-10-20T02:17:12Z，DECISION）
> 回执：评论 `6281971845`（2026-10-20T02:32:30Z，ACK）
> 执行方：N1 WorkBuddy
> 主测量窗口：2026-10-19 23:58:38–2026-10-20 00:01:02 本地（15:58–16:01Z）
> 补充只读复核：2026-10-20 10:50–10:54 本地（02:50–02:54Z）
> 状态：只读 / 零删除 / HOLD 持续
> 公开口径：本文件不含真实用户名、主机名、个人文件名、原始目录清单或凭证；路径一律用占位符。

---

## 0. 结论先行

1. **分区层闭合（PROVEN）**：物理盘 476.94 GiB = 7 个分区合计 475.971 GiB + 未分配间隙 0.969 GiB。没有发现未列出的分区；但除 C:/D: 外的 5 个隐藏分区**没有做过文件级枚举**。
2. **卷层 C: 未闭合（PARTIAL）**：C: 已用 152.637 GiB，枚举 107.535 GiB，**残余 45.102 GiB**。D: 已用 117.091 GiB，枚举 116.333 GiB，残余 0.758 GiB（0.65%），对决策目的可视为闭合。
3. **文件层覆盖（PROVEN + 已知缺口）**：C:+D: 共 1,181,242 个文件 / 223.868 GiB（已按卷内 inode 去重）；96,815 次硬链接重复命中、19.887 GiB 未重复计入；1,439 个 reparse 点记录但未展开；**503 个目录拒绝访问**（C: 501 + D: 2），其体积 **UNKNOWN**；stat 错误 0。
4. **45.102 GiB 不是可删空间**。已证明计入枚举的 pagefile/swapfile/DumpStack 不在残余内；休眠文件不存在；USN 已排除为大头。残余主体最可能落在 VSS/SVI、NTFS 元数据、簇分配差与拒绝目录，但**没有任何一项已被证明可回收**。
5. **本报告更正了三项既有陈述**：WER 归因（不是 401 个 `AppCrash_dwm`）、隐藏分区总量（不是 21.17 GiB）、持续写入换算（不是 0.14–0.33 GiB/天）。详见 §附录 C。
6. **下一步不是清理，而是一次性提权只读采集**。所需命令、各自解锁项、明确禁止的写动作已列在 §12，可一次批够。

---

## 1. 对账层级与证据等级

| 层 | 对象 | 本次方法 | 证据等级 | 主要缺口 |
|---|---|---|---|---|
| L0 | 物理盘 / 分区表 | `Get-Disk` / `Get-Partition` / `Get-Volume` | **PROVEN** | 隐藏分区内容未枚举 |
| L1 | C:/D: 卷级已用 | 括号式同时点测量（枚举前后各取卷读数） | **PROVEN** | 测量后仍持续写入 |
| L2 | 文件枚举 | `tools/disk_scan.py`，按卷内 `(dev, ino)` 去重 | **PROVEN** + 已知缺口 | reparse 目标、拒绝目录、ADS/压缩/稀疏 |
| L3 | 文件系统元数据 / 分配差 | `fsutil usn queryjournal`；其余命令被拒 | **PARTIAL / UNKNOWN** | $MFT、簇大小、allocation report |
| L4 | VSS / 系统还原 | `vssadmin` / WMI / 还原点查询 | **UNRECOVERABLE（未提权）** | 影子副本总量与日期 |
| L5 | 拒绝目录 | 逐目录记录 + 路径分类 | **UNKNOWN（体积）** | 503 个目录一个都没测到体积 |
| L6 | 未枚举 / 排除树 | 扫描器行为 + `gov` 策略清单 | **PROVEN（清单）/ UNKNOWN（体积细分）** | 名称级排除、云挂载、候选口径差 |
| L7 | 时间覆盖 / 快照谱系 | 9 份候选快照 + manifest | **PROVEN（谱系）/ UNRECOVERABLE（装机时刻）** | 2025-09-26→2026-09-22 无快照 |
| L8 | 候选受管全集 | `gov` 候选快照 631,213 条 | **PARTIAL（索引口径）** | 不去重硬链接、排除树、时间不同点 |

证据等级定义：

- **PROVEN**：本机直接测量、可复现，误差来源已量化。
- **PARTIAL**：有测量，但存在已知未测部分，不能当作闭合值。
- **UNKNOWN**：未测量；不得用典型值或常识替代。
- **ESTIMATE**：模型推算，必须带区间，不得反推伪精确值。
- **UNRECOVERABLE**：当前权限/证据路径下无法取得，不是“暂时没算”。

---

## 2. L0：物理盘与分区（PROVEN）

### 2.1 物理盘

| 项 | 值 | 证据 |
|---|---|---|
| 磁盘 | NVMe WD 512GB，GPT，Disk 0 | `Get-Disk`（2026-10-20 10:50 本地） |
| 原始容量 | 512,110,190,592 B = **476.94 GiB** | 同上 |
| 分区总数 | 7 | `Get-Partition` |

### 2.2 分区表（全部为同一块物理盘）

| # | 类型 | 盘符 | 大小（GiB） | 已用（GiB） | 可用（GiB） | 本次是否文件枚举 |
|---|---|---|---:|---:|---:|---|
| 1 | System / ESP | — | 0.391 | UNKNOWN | UNKNOWN | 否 |
| 2 | Reserved (MSR) | — | 0.125 | 保留，无文件系统 | — | 否 |
| 3 | Basic | **C:** | **198.969** | **152.637**（00:01） | 46.331（00:01） | 是 |
| 4 | Recovery | — | 1.031 | 0.975 | 0.057 | 否 |
| 5 | Basic | **D:** | **257.054** | **117.091**（00:01） | 139.963（00:01） | 是 |
| 6 | Recovery / Image | — | 16.953 | 16.811 | 0.143 | 否 |
| 7 | Recovery / DELLSUPPORT | — | 1.448 | 1.015 | 0.433 | 否 |
| — | 未分配间隙 | — | **0.969** | — | — | 否 |
| — | **合计** | — | **476.940** | — | — | — |

分区算术：

```
7 个分区合计        475.971 GiB
未分配间隙            0.969 GiB
                     ----------
                    476.940 GiB  = 物理盘容量
```

未分配间隙明细：

- 盘首 1 MiB（分区 1 之前）；
- D: 结束到 Image 分区开始之间 **0.9668 GiB**（分区表没有覆盖这一段）；
- Image 与 DELLSUPPORT 之间 1 MiB；
- 盘尾 0.0003 GiB。

**结论（PROVEN）**：分区表层面没有“看不见的分区”。0.9668 GiB 的间隙在分区表中没有对应项，身份按 **UNKNOWN** 处理（可能是未分配空间，也可能是厂商工具留下的非分区区域），本次未读写。

### 2.3 隐藏分区的性质

- Image 16.953 GiB：厂商恢复映像，已用 16.811 GiB，接近满。
- DELLSUPPORT 1.448 GiB：厂商支持/诊断分区，已用 1.015 GiB。
- Recovery 1.031 GiB：Windows 恢复环境，已用 0.975 GiB。
- ESP 0.391 GiB、MSR 0.125 GiB：启动/保留区，未做文件枚举。
- 这些分区**均未授权删除**，也不属于本次“清理”讨论范围；任何处置都需要单独说明对恢复能力的影响。

### 2.4 更正：隐藏分区总量不是 21.17 GiB

旧基线写“1.03 / Image 18.20 / DELLSUPPORT 1.55 / ESP 0.39 GiB”。其中 Image 与 DELLSUPPORT 用的是十进制 GB 数值，却被标成了 GiB；且漏掉了 128 MiB 的 MSR。按二进制 GiB 重算：

| 分区 | 旧写法 | 实测（GiB） |
|---|---:|---:|
| Recovery | 1.03 | **1.031** |
| Image | 18.20 | **16.953** |
| DELLSUPPORT | 1.55 | **1.448** |
| ESP/System | 0.39 | **0.391** |
| MSR | 未列 | **0.125** |
| **合计** | **21.17（错）** | **19.948** |

这是一个**单位混用错误**，不是测量变化。旧值高估 1.22 GiB。隐藏分区未分配文件枚举这一事实不变。

---

## 3. L1：卷级空间（PROVEN / PARTIAL）

### 3.1 括号式同时点测量

采集：`.state/scan/20261019_235838_volume_bracket.json`（23:58:38–00:01:02，143.9 秒）。

| 卷 | 总容量（GiB） | used_before | used_after | drift | used_mid | 枚举（GiB） | 文件数 | 残余（GiB） |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C: | 198.969 | 152.637 | 152.637 | +0.18 MiB | **152.637** | **107.535** | 694,708 | **45.102** |
| D: | 257.054 | 117.091 | 117.092 | +0.73 MiB | **117.091** | **116.333** | 486,534 | **0.758** |
| 合计 | 456.023 | — | — | — | **269.728** | **223.868** | 1,181,242 | **45.860** |

公式：

```
residual = used_mid − enumerated
used_mid = (used_before + used_after) / 2
```

drift 0.18 / 0.73 MiB，说明扫描 2 分 24 秒内卷读数基本静止；这解决的是“卷读数与枚举不同时点”的问题，不解决扫描之后继续写入的问题。

### 3.2 闭合程度

- C: 残余占已用 **29.55%**（45.102 / 152.637）→ **PARTIAL**，不能当作已解释。
- D: 残余占已用 **0.65%**（0.758 / 117.091）→ 对决策目的可视为闭合；仍受 §5 的元数据/ADS 缺口约束，不是字节级精确。

### 3.3 已证明计入、不得重复扣减的项

| 项 | 00:01 体积 | 证据 |
|---|---:|---|
| `C:\pagefile.sys` | **16.287 GiB** | 修复 `(dev, ino)=(0,0)` 去重碰撞后计入；files=1 / hl=0 / no_identity=1 |
| `C:\swapfile.sys` | **0.016 GiB** | 同上 |
| `C:\DumpStack.log.tmp` | 12,288 B | 同上，C: 共 3 个 no_identity 文件 |
| `C:\hiberfil.sys` | **不存在** | `powercfg /a`：休眠尚未启用；`Get-Item` ItemNotFound（缺失，不是拒绝） |

pagefile 由系统托管（`AutomaticManagedPagefile=True`），体积会变。**它永远是“已计入项”，不是残余。**

---

## 4. L2：文件枚举（PROVEN + 已知缺口）

### 4.1 方法

`tools/disk_scan.py` 的行为：

- 遍历 C:/D:，`os.scandir` 迭代，不递归 Python 栈；
- 长路径（>260 字符）走 `\\?\` 前缀，不静默跳过；
- 硬链接按**卷内** `(st_dev, st_ino)` 去重，重复命中单独计数；
- reparse 点（symlink/junction）**记录但不展开**，避免经链接双算；
- 目录权限错误逐目录记录，不致命；
- 聚合为 914 个 bucket，不保留逐文件清单。

### 4.2 硬链接

| 卷 | 硬链接重复命中 | 重复字节（GiB） | 说明 |
|---|---:|---:|---|
| C: | 68,411 | **19.391** | 主要是 WinSxS / System32 / 组件存储 |
| D: | 28,404 | **0.495** | 应用目录 |
| 合计 | **96,815** | **19.887** | 已在枚举总量中剔除，不重复计 |

**关键限定**：只有**卷级总量**是去重不变量；bucket 间的字节归属取决于遍历顺序。WinSxS 16.598 GiB、System32 5.164 GiB 是“该 bucket 首次见到的 inode 之和”，不能拿来和另一套扫描器的同名 bucket 直接比。

### 4.3 覆盖指标

| 卷 | 访问目录数 | 测得文件数 | 目录拒绝 | 每千目录拒绝率 | stat 错误 |
|---|---:|---:|---:|---:|---:|
| C: | 245,162 | 694,708 | **501** | 2.0 | 0 |
| D: | 73,984 | 486,534 | **2** | 0.0 | 0 |
| 合计 | 319,146 | 1,181,242 | **503** | 1.6 | 0 |

### 4.4 本层看不见的部分

1. 503 个拒绝目录的全部内容（§7）。
2. 1,439 个 reparse 点的目标树（C: 108 / D: 1,331）；本次只记录不展开，且 `disk_scan.py` 不保留其路径清单 → 目标身份 **UNKNOWN**。
3. C:/D: 之外的卷（隐藏分区、可移动盘、网络盘）。
4. NTFS Alternate Data Streams（ADS）：`st_size` 不含 ADS 占用。
5. 压缩/稀疏文件：`st_size` 是逻辑大小，不是本地分配大小；云同步占位文件也可能只有逻辑大小。
6. 扫描开始前已删除、且不在回收站/影子副本中的文件。

---

## 5. L3：文件系统元数据与分配差（UNKNOWN / PARTIAL）

### 5.1 已测

| 项 | 结果 | 证据等级 |
|---|---|---|
| USN 日志上限 | 128 MiB | **PROVEN** |
| USN 分配差 | 32 MiB | **PROVEN** |
| USN 为大头 | 已排除 | **PROVEN** |
| pagefile / swapfile 身份 | `dev=0, ino=0`，按无身份文件计一次 | **PROVEN** |
| 硬链接去重 | 卷内 `(dev, ino)` 全局生效 | **PROVEN** |

### 5.2 未测

| 项 | 状态 | 解锁命令 |
|---|---|---|
| $MFT 大小、记录数、属性占用 | **UNKNOWN** | `fsutil fsinfo ntfsinfo C:` |
| 簇大小、总/空闲簇 | **UNKNOWN** | 同上 |
| 卷分配报告（按类别拆 allocated） | **UNKNOWN** | `fsutil volume allocationreport C:` |
| $LogFile / $Bitmap / $Secure / $Extend / 目录索引 | **UNKNOWN** | allocation report |
| 压缩/稀疏/ADS 的实际分配差 | **UNKNOWN** | allocation report |
| WinSxS 真实可回收量 | **UNKNOWN** | `dism /Online /Cleanup-Image /AnalyzeComponentStore` |
| Windows 保留存储（Reserved Storage） | **UNKNOWN** | DISM / 组件存储分析 |

### 5.3 可以对残余贡献做出多大？

只能给**有明确前提的上界**，不能给“估计值”冒充测量值：

- **簇尾碎片**：1,181,242 个文件；若簇大小为 4 KiB，每个文件最多浪费 4,095 B，理论上限 **4.51 GiB**。簇大小本身未测（`fsutil` 被拒），所以这是条件上界，不是本机实测值。
- **$MFT**：每文件 1 KiB 记录是常见布局，1,181,242 条至少约 1.13 GiB；实际记录大小、属性、目录索引未测 → **ESTIMATE 1–3 GiB 量级**，不得写进“已解释”栏。
- **NTFS 其他元数据**：通常远小于 1 GiB，但本机未测 → UNKNOWN。
- **VSS/SVI**：未测，可能是最大单项（§6）。
- **拒绝目录**：未测，可能含 GiB 级崩溃转储（§7）。

这些项相加仍无法证明 45.102 GiB 的完整构成；**必须等 allocation report + VSS 查询 + DISM**。

---

## 6. L4：VSS 与系统还原（UNRECOVERABLE，未提权）

### 6.1 已固化的拒绝证据

| 命令 | 结果 |
|---|---|
| `vssadmin list shadowstorage` | exit=2，“你没有正确的权限” |
| `vssadmin list shadows` | 同上 |
| `fsutil volume allocationreport C:` | 错误 5，拒绝访问 |
| `fsutil fsinfo ntfsinfo C:` | 错误 5，拒绝访问 |
| `dism /Online /Cleanup-Image /AnalyzeComponentStore` | 错误 740，需要提升 |
| `Get-ComputerRestorePoint` | 拒绝访问 |
| `Win32_ShadowStorage` / `Win32_ShadowCopy` | CimException |
| `fsutil usn queryjournal C:` | **成功**（128 MiB 上限 / 32 MiB 分配差） |

### 6.2 补充只读观察（不能替代 VSS 查询）

- `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore`：`RPSessionInterval=1`，`DisableSR` 未设置。
- `Get-Service VSS`：服务存在，状态 **Stopped**，启动类型 **Manual**。
- 这些只说明“系统还原/VSS 组件存在且未被策略禁用”，**不说明影子副本占用了多少空间**。
- `C:\System Volume Information` 与 `D:\System Volume Information` 均在拒绝目录中；其体积 **UNKNOWN**。

### 6.3 结论

- SVI 可能包含影子副本、系统还原点、索引/配额元数据；其中影子副本是 45.102 GiB 残余最可能的**单项**。
- 但“最可能”不是测量结论；在没有 `vssadmin list shadowstorage` 之前，**不得**把残余写成“影子副本占用 45 GiB”，也不得据此删除任何还原点。

---

## 7. L5：503 个拒绝目录（体积 UNKNOWN）

### 7.1 总量与分布

| 卷 | 拒绝数 | WinError | 说明 |
|---|---:|---|---|
| C: | **501** | 全部 5（拒绝访问） | 系统/服务/崩溃归档 |
| D: | **2** | 全部 5 | SVI + 系统 SID 回收站 |
| 合计 | **503** | 全部 5 | 无其他错误类型 |

C: 按路径身份分组：

| 组 | 数量 | 备注 |
|---|---:|---|
| WER（ReportArchive + Temp） | **401** | 其中 400 个 ReportArchive + 1 个 Temp |
| Defender | 11 | Definition Updates / Scans / Quarantine / Support / NIS |
| Windows/System32 | 19 | sru、config、spool、Tasks、LogFiles\WMI、DriverState、SleepStudy、WDI 等 |
| Windows/SysWOW64 | 8 | tasks、sru、config、msdtc、com\dmp 等 |
| Windows/AppCompat | 5 | 兼容性数据库/状态 |
| Windows/其他 | 11 | WUModels、Temp、ServiceProfiles、security、PLA 等 |
| ProgramData/其他 | 23 | Windows Holographic、Master Store、Packages 等 |
| ProgramData/Dell | 5 | 厂商服务状态 |
| Users/\<user\>/AppData | 2 | 用户级服务状态 |
| Program Files | 1 | WindowsApps 子目录 |
| C: 根其他 | 4 | Config.Msi、PerfLogs、Recovery 等 |
| `$Recycle.Bin` 系统 SID | 2 | S-1-5-18 / 管理员 SID |
| System Volume Information | 1 | VSS/SVI |
| **合计** | **501** | — |

D: 2 条：

- `D:\System Volume Information`（VSS/SVI）
- `D:\$RECYCLE.BIN\S-1-5-18`（系统 SID 回收站）

### 7.2 WER 明细：更正“401 个 AppCrash_dwm”

400 个 `ReportArchive` 归档按名称分类：

| 类 | 数量 |
|---|---:|
| `AppCrash_*` | **215** |
| `NonCritical_*` | **168** |
| `Critical_*` | **14** |
| `Kernel_*` / `AppHang_*` 等 | **3** |
| **合计** | **400** |

`AppCrash_*` 的前几名：

| 可执行文件 | 归档数 |
|---|---:|
| `FusionService.exe` | **56** |
| `CT01Service.exe` | **42** |
| `MSOfficePLUSService` | **22** |
| `wpp.exe` | **19** |
| `dwm.exe` | **17** |
| `AweSun.exe` | **15** |
| `svchost.exe` | **12** |
| `Explorer.EXE` | **6** |
| 其余（LogonUI、AppUp.IntelArc、MsMpEng、MoUsoCoreWorker、LetsPRO、OpenAI.Codex 等） | 26 |
| **合计** | **215** |

**更正**：

- 旧说法“401 个是 `AppCrash_dwm.exe_*`”不成立。401 是 **WER 相关拒绝总数**（400 个归档 + 1 个 Temp 目录）；`dwm.exe` 只有 **17** 个。
- 系统稳定性信号比“dwm 反复崩溃”更宽：`FusionService`、`CT01Service`、`MSOfficePLUSService`、`wpp`、`AweSun` 的归档数都高于 dwm。这是待提权后做类别级诊断的对象，不是空间清理依据。

### 7.3 为什么不能给上界

- 503 个目录的体积**一个都没测到**。任何“WER 单个通常几 MB，所以总量不超过 X GiB”都是典型值外推，不是本机测量。
- WER 归档可能包含完整崩溃转储（`.dmp` 可到数 GiB）；Defender `Definition Updates` 也可能到数百 MB。
- 拒绝目录里还有 SVI、回收站系统 SID、WindowsApps 子目录；不能按“系统目录通常不大”处理。
- **唯一可辩护的表述**：按路径身份，这 503 个目录**不太可能**构成 45.102 GiB 的主体；这是弱判断，不是上界证明。

---

## 8. L6：未枚举与排除树

### 8.1 `disk_scan.py` 的未枚举（物理层）

| 类别 | 数量/状态 | 证据等级 |
|---|---|---|
| 拒绝目录 | 503 个，体积 UNKNOWN | UNKNOWN |
| reparse 点目标 | 1,439 个（C: 108 / D: 1,331），未展开 | UNKNOWN（身份未保留） |
| 隐藏分区文件 | 5 个分区，未枚举 | UNKNOWN |
| 未分配间隙 | 0.969 GiB，无分区 | UNKNOWN |
| ADS / 压缩 / 稀疏 / 云占位 | 未测 | UNKNOWN |
| C:/D: 之外 | 网络盘、可移动盘、其他卷 | 不在范围 |
| 已删除且不在回收站/影子副本 | — | UNRECOVERABLE |

### 8.2 候选快照（`gov`）的排除树

`gov` 候选快照使用名称级/前缀级排除，和 `disk_scan` 不是同一口径：

| 层 | 排除项 |
|---|---|
| COMMON `hard_skip` | `$Recycle.Bin`、`System Volume Information`、`WinSxS`、`Assembly`、`InstallerCache`、`node_modules`、`__pycache__`、`.git`、`Temp` |
| ROLE_DATA | `.Trash`、`$RECYCLE.BIN`、`node_modules`、`.venv`、`venv`、`.gradle`、`.gbrain`、`site-packages` |
| ROLE_SYSTEM | `WinSxS`、`SoftwareDistribution`、`WindowsApps`、`Package Cache`、`Prefetch`、`Recovery`、`perflogs`、`Downloaded Program Files` |
| MACHINE | `.state`、`.gov-managed`、`.uploads`、`CrashDumps` |
| 云挂载（按目录名） | `<cloud-sync>`（坚果云）、`Nutstore`、`WPSDrive`、`OneDrive`、`Dropbox`、`iCloudDrive` |
| 云前缀 | `D:\<data-root>\WPSDrive`、`C:\Users\<user>\WPS Cloud Files`、`WPS Cloud`、`Nutstore`、`OneDrive` |
| 冻结前缀 | `D:\<data-root>\_restore`、`_cleanup_scripts`、`WXWork` |

注意：`hard_skip` 是**目录名匹配**，所以任何名为 `Temp` 的目录（不只 `C:\Windows\Temp`）都会被候选扫描跳过；这是候选全集比磁盘枚举小的原因之一。

### 8.3 候选全集 vs 磁盘枚举：总量接近，但不能互减

| 项 | 值 |
|---|---:|
| `disk_scan` 去重枚举 | **223.868 GiB** / 1,181,242 文件 |
| 候选快照（10-19 20:48） | **163.313 GiB** / 631,213 条 |
| 差值 | **60.555 GiB** |
| 八棵已知整树排除：`<cloud-sync>` 19.307 + WinSxS 16.598 + C 回收站 4.319 + D 回收站 9.104 + WXWork 7.908 + `.state` 3.009 + `_restore` 0.311 + `_cleanup_scripts` 0.010 | **60.566 GiB** |
| 表面闭合差 | **+0.011 GiB（约 11 MiB）** |

**这个 11 MiB 的闭合不能当作“候选全集 = 磁盘枚举减去八棵树”的证明。** 原因：

1. 候选扫描器 `gov/inventory.py` 直接累加每个路径的 `st_size`，**没有按 inode 去重硬链接**；磁盘枚举按 `(dev, ino)` 去重，重复字节为 **19.887 GiB**。
2. 候选扫描器又按名称排除了 `Temp`、`node_modules`、`.venv`、`.git`、`site-packages` 等目录；这些排除的体积与硬链接重复量同量级（约 20 GiB）。
3. 两边时间不同（20:48 vs 23:58），pagefile 等动态文件会变。
4. 逐 bucket 对比中，候选值高于磁盘值的部分合计 **+17.4 GiB**（如 `Program Files (x86)/Microsoft` +8.36、`System32` +3.89），低于磁盘值的部分合计 **−17.4 GiB**；这是硬链接归属顺序 + 名称排除 + 时间差共同造成的，**桶级不可互比**。

**结论**：候选全集是**索引口径**（per-path sum），磁盘枚举是**卷内去重口径**；两者只在“经过八棵整树排除 + 硬链接/名称排除相互抵消”后表面接近。**禁止用 163.313 GiB 去反推物理占用，也禁止用它和 223.868 GiB 直接做删除量计算。**

### 8.4 对增长账本（下一步交付）的影响

- 10-11→10-19 的同口径 diff **只覆盖候选全集**；它不覆盖 `<cloud-sync>` 19.31 GiB、WinSxS 16.60 GiB、回收站 13.42 GiB、WXWork 7.91 GiB、`.state` 3.01 GiB、`_restore`/`_cleanup_scripts` 0.32 GiB。
- 企业微信树 10-19 冻结后从候选快照消失（18,159 条 / 7.73 GiB），已标 `PATH_GONE_CALIBER`，不是删除。
- `net_index_delta +34.08 GiB` 是**索引算术**，不是物理增长；硬链接重复与排除树会同时影响它。
- 下一份 `growth-ledger.md` 必须写明“本账本不覆盖上述树”，否则会被误读成全盘增长。

---

## 9. L7：时间覆盖与快照谱系

### 9.1 硬事实

| 事实 | 值 | 证据 |
|---|---|---|
| Windows 安装 | 2025-09-26 15:41:26 | `Win32_OperatingSystem.InstallDate` |
| 用户 profile 最后写入 | 2025-09-26 15:36:41 | `C:\Users` LastWriteTime |
| 最近启动 | 2026-10-16 14:59:04 | `Win32_OperatingSystem.LastBootUpTime` |
| 最早可枚举快照 | 2026-09-22（555,110 条 / 140.76 GiB，过滤口径） | `.state/candidates/2026-09-22.jsonl.gz` + manifest |
| 装机→最早快照的空窗 | **约 361 天** | 2025-09-26 → 2026-09-22 |
| 首个可同口径 diff 的家族 | 2026-10-03 起 | `20261003_143307` 及之后 |
| 唯一已验证同口径窗口 | 2026-10-11 18:04 → 2026-10-19 20:48（8.11 天） | `snapshot_diff` r3 |

### 9.2 快照口径矩阵

| 快照 | 条目 | GiB | 口径 | 可否同口径 diff |
|---|---:|---:|---|---|
| 2026-09-22 | 555,110 | 140.76 | 过滤，**不含用户文档目录** | 否 |
| 2026-09-27 | 515,632 | 133.30 | 同上 | 否 |
| 2026-10-03（gz） | 1,630,675 | 431.22 | 更宽（含更多系统区，仍无 WinSxS/回收站/文档） | 否 |
| 2026-10-03 14:33 | 550,954 | 146.40 | 过滤（含文档） | 家族起点 |
| 2026-10-04 10:29 | 523,735 | 137.53 | 同 | 是 |
| 2026-10-04 21:03 | 524,974 | 137.72 | 同 | 是 |
| 2026-10-11 18:04 | 530,337 | 136.97 | 同 | 是 |
| 2026-10-14 15:17 | 360,915 | 89.61 | 窄（缺 DiskMigration 与 Program Files 全树） | 否（离群） |
| 2026-10-19 20:48 | 631,213 | 163.31 | 同 + 企业微信树新冻 | 是 |

### 9.3 结论

- **2025-09-26 装机时刻的逐文件状态不存在**；`CLEAN_INSTALL_MODEL` / `MINIMUM_USABLE_MODEL` 只能是估计模型，必须带证据等级与误差区间。
- **最早可枚举快照 2026-09-22 也不是全覆盖**：它排除了用户文档目录；因此“早于 09-22 的差异”不能被严格 Diff。
- 目前唯一可辩护的同口径增量是 2026-10-11→2026-10-19 的候选全集差分；它不覆盖 §8.4 列出的树。
- 任何“从 2025 装机到现在新增了多少”的全盘结论，当前都 **UNRECOVERABLE**，只能给候选口径的下界/局部证据。

---

## 10. 45.102 GiB 残余的分解边界（不宣布可回收）

### 10.1 能排除的

| 项 | 状态 |
|---|---|
| pagefile 16.287 GiB | 已计入枚举，不在残余内 |
| swapfile 0.016 GiB | 已计入枚举 |
| DumpStack.log.tmp 12 KB | 已计入枚举 |
| hiberfil.sys | 不存在（休眠未启用） |
| USN 日志为大头 | 已排除（上限 128 MiB / 分配差 32 MiB） |

### 10.2 可能贡献，但未测

| 项 | 量级/状态 | 证据等级 |
|---|---|---|
| VSS/SVI 影子副本 + 系统还原 | UNKNOWN；最可能的最大单项 | UNKNOWN |
| $MFT + NTFS 元数据 | ESTIMATE 1–3 GiB 量级 | ESTIMATE |
| 簇尾碎片 | 条件上界 4.51 GiB（4 KiB 簇假设） | 条件上界，非实测 |
| 503 个拒绝目录 | UNKNOWN，可能含 GiB 级 .dmp | UNKNOWN |
| Windows 保留存储 / 组件存储 | UNKNOWN | UNKNOWN |
| 压缩/稀疏/ADS 分配差 | UNKNOWN，方向可正可负 | UNKNOWN |
| 已分配未入索引 / 簇级保留 | UNKNOWN | UNKNOWN |

### 10.3 结论

- 45.102 GiB 是 **PARTIAL**：已证明其中没有“页面文件重复解释”这一项，但无法证明任何一项可回收。
- **不得**把它写成“系统占用”“影子副本占用”或“垃圾”；这三种说法都超出了证据。
- 在提权采集前，任何清理方案都只能把 45.102 GiB 列为 **UNKNOWN，不纳入可释放量**。

---

## 11. 动态性：00:01 基线 vs 10:51 只读复核

> 这一节只证明“机器在测量后继续变化”，不是新的残余测量；没有重跑全盘枚举。

| 项 | 2026-10-19 23:58–00:01 | 2026-10-20 10:51 | 变化 |
|---|---:|---:|---:|
| C: 已用 | 152.637 GiB | **159.697 GiB** | **+7.060 GiB** |
| C: 可用 | 46.331 GiB | **39.272 GiB** | −7.059 GiB |
| D: 已用 | 117.091 GiB | **116.846 GiB** | −0.245 GiB |
| `C:\pagefile.sys` | 16.287 GiB | **18.306 GiB** | **+2.019 GiB** |
| `C:\swapfile.sys` | 0.016 GiB | 0.016 GiB | 0 |
| C: 其他净变化 | — | — | **+5.041 GiB（未归因，未重新枚举）** |

- 时间跨度 10 小时 50 分（约 10.83 h），C: 平均 **+0.652 GiB/h**；pagefile 占 C: 增长的 **28.6%**，其余 5.04 GiB 未归因。
- 这再次证明：pagefile 是系统托管、会伸缩的已计入项；**任何“清理后可用空间”都不能写成稳态值**。
- 也证明 00:01 的 45.102 GiB 是**冻结时刻的残余**；如果现在重跑，残余会因新写入而变化。本报告不把 10:51 的数字与 00:01 的枚举相减。

### 11.1 更正：持续写入换算

旧文档写“100–230 MB/min ≈ 0.14–0.33 GiB/天”。这个换算错误放大了约 1000 倍：

```
100 MB/min × 60 / 1024 = 5.86 GiB/h
230 MB/min × 60 / 1024 = 13.48 GiB/h
若 24h 持续：140.6–323.5 GiB/天
```

正确表述应为：

- **活跃期**：100–230 MB/min = **5.86–13.48 GiB/h**；
- **本次 10.83 h 实测平均**：C: **+0.652 GiB/h**（含空闲，不等于活跃期速率）；
- **不得**把活跃期速率线性外推为日速率；也不得再用“0.14–0.33 GiB/天”。

这条更正影响 `baseline.md`、`bridge-ledger.md` 以及任何引用“持续写入 0.14–0.33 GiB/天”的清理情景。

---

## 12. 一次性提权只读采集清单（请一次批够）

### 12.1 要的权限

| # | 权限 | 目的 | 边界 |
|---|---|---|---|
| 1 | 本地管理员提升（一次 UAC） | 运行 `vssadmin` / `fsutil` / `dism` 只读分析 | 只开一个进程，不常驻、不新建服务/计划任务 |
| 2 | 进程内启用 `SeBackupPrivilege` | 读取 503 个拒绝目录与 SVI 的体积/文件数 | **不改 ACL、不 takeown、不改所有权** |
| 3 | 读取 `System Volume Information` | 拆分 VSS/SVI | 只读列目录 + 统计，不打开/恢复/删除影子副本 |

明确**不申请**：删除、移动、重命名、格式化、卸载、停服务、改注册表、改 ACL、创建/删除还原点、`dism /StartComponentCleanup`、重启、联网上传。

### 12.2 命令与解锁项

| # | 命令（全部只读） | 解锁什么 | 失败时的处理 |
|---|---|---|---|
| 1 | `vssadmin list shadowstorage` | 每卷影子副本占用；拆分 SVI 与 45.102 GiB | 记录拒绝，维持 UNKNOWN |
| 2 | `vssadmin list shadows` | 影子副本清单、创建时间；判断是否存在 2025-09-26 之后的旧版本 | 同上 |
| 3 | `fsutil volume allocationreport C:` | 按类别拆 allocated；闭合残余 | 同上 |
| 4 | `fsutil fsinfo ntfsinfo C:` | $MFT 大小、簇大小、总/空闲簇 | 同上 |
| 5 | `fsutil fsinfo volumeinfo C:` | 卷标志、簇大小交叉验证 | 同上 |
| 6 | `dism /Online /Cleanup-Image /AnalyzeComponentStore` | WinSxS 真实可回收量；保留存储线索 | **只 Analyze，不 Cleanup** |
| 7 | `Get-ComputerRestorePoint` + `Get-CimInstance Win32_ShadowCopy` | 还原点数量/日期；与 vssadmin 交叉验证 | 同上 |
| 8 | 对 503 条拒绝路径逐条 `Get-ChildItem -Force -Recurse \| Measure-Object Length -Sum`（或 `robocopy /L /S /NJH /NJS /BYTES` 列表模式） | 拒绝目录的真实体积、WER `.dmp` 数量 | 单条失败记 UNKNOWN，不 takeown |
| 9 | `Get-WinEvent` Application 日志中 WER/dwm/FusionService 事件 | 稳定性诊断（非空间） | 可选 |
| 10 | `fsutil usn queryjournal D:` | D: USN 上限交叉验证 | 可选 |

建议输出到 `.state/elevated/20261020/`：

```
vss_shadowstorage.txt
vss_shadows.txt
fsutil_allocationreport_C.txt
fsutil_ntfsinfo_C.txt
fsutil_volumeinfo_C.txt
dism_analyze_componentstore.txt
restore_points.json
denied_dirs_sizes.jsonl
win_event_summary.json
usn_D.txt
run_manifest.json        # 命令、时间、退出码、是否成功
```

### 12.3 提权后仍然 UNKNOWN 的

即使上述全部成功，以下仍然无法闭合：

1. **2025-09-26 装机时刻的逐文件状态**：VSS 影子副本通常不会保留 13 个月；若没有，就是 UNRECOVERABLE。
2. **已删除且不在回收站/影子副本中的文件**：无法枚举。
3. **云同步在线占位文件**：本地 allocation 与逻辑大小可能不一致；需要云服务商 API，不是本机提权能解决。
4. **逐文件唯一副本证明**：需要内容哈希/来源证据；本次只做空间，不做哈希战役。
5. **WinSxS 组件级可回收量**：DISM 只给汇总，不给每个组件的删除影响。
6. **簇级碎片/ADS 的逐文件归因**：allocation report 能给类别总量，不一定给逐文件拆分。

### 12.4 安全闸

- 采集脚本只允许 `list` / `query` / `info` / `analyze` / `-Recurse` 只读统计；
- 禁止字符串：`delete`、`remove`、`cleanup`、`StartComponentCleanup`、`takeown`、`icacls`、`format`、`diskpart clean`；
- 运行前后各取一次卷读数，写入 `run_manifest.json`，证明采集窗口内没有删除造成的 free 突增；
- 输出先落本地 `.state/elevated/`，脱敏后才允许进入公开仓库。

---

## 13. 本地阶段文件是否切换

### 13.1 现状

- `.state/PHASE_SCAN_BASELINE` 仍在盘上，内容仍为只读阶段标记。
- `tools/housekeep_exec.py` **只检查这个文件是否存在**；存在即拒绝任何清理执行。
- 它是当前唯一被代码强制的“零删除”闸；改名/删除它会在不改代码的情况下静默放开 housekeep。

### 13.2 建议：暂不切换，先改门禁

**不建议**现在把旧 marker 改名或删除来“表示阶段已切换”。理由：

1. 新阶段 `INITIAL_STATE_RECONSTRUCTION` 仍然是只读/HOLD；旧 marker 的**行为语义正确**，只是名字过期。
2. 只改文件名、不改 `housekeep_exec.py`，会让门禁失效；这是安全风险，不是文案问题。
3. 本报告仍是分析交付，不是执行开工。

**建议的切换步骤（下次开工时一次完成）**：

1. 修改 `tools/housekeep_exec.py`：把门禁改为“`PHASE_SCAN_BASELINE` **或** `PHASE_INITIAL_STATE_RECONSTRUCTION` 任一存在 → 只读”。
2. 新增 `.state/PHASE_INITIAL_STATE_RECONSTRUCTION`，写入：
   - `phase=INITIAL_STATE_RECONSTRUCTION`
   - `decision_comment=6281781711`
   - `ack_comment=6281971845`
   - `owner=N1 WorkBuddy`
   - `read_only=true`
   - `deletion_authorized=false`
   - `created_at=...`
3. **保留** `.state/PHASE_SCAN_BASELINE`，不删除、不改名；它作为历史门禁继续生效。
4. 跑一次 `housekeep_exec` 的健康/ dry-run，确认两个 marker 任一存在都拒绝执行。
5. 只有在两个 marker 都在、门禁代码已更新、测试通过后，才在文档里写“阶段已切换”。

这样既让阶段标签跟上 DECISION，又不会在切换瞬间放开删除闸。

---

## 14. 与 DECISION 五点的对应关系

| DECISION 要求 | 本报告覆盖 | 状态 |
|---|---|---|
| 1. 全盘扫描完整性：卷级/隐藏分区/FS 元数据/VSS/503 拒绝目录/未枚举与排除树，逐层列已测、未测、UNKNOWN | §2–§9 | **本报告完成** |
| 2. 区分 INITIAL_HISTORICAL_FACT / CLEAN_INSTALL_MODEL / MINIMUM_USABLE_MODEL / TODAY，C/D 分列，证据等级和误差 | §9 给出时间事实与模型边界；四分类主报告待写 | **下一步** |
| 3. 增长账本：同口径严格 Diff，按来源汇总，Top100；公开只放匿名聚合 | §8.4 给出候选口径的覆盖边界；账本待写 | **待交付** |
| 4. 六类处置标签、预计释放、影响、唯一副本/凭据风险、恢复成本、分批一键授权 | 本报告只给证据边界；清理清单/三方案对比待写 | **待交付** |
| 5. 一次只读提权采集清单，让运营方一次批够 | §12 | **本报告完成** |

交付状态：`reports/scan-completeness-reconciliation.md` = **1/6**（本报告）。下一份：`reports/INITIAL_STATE_VS_TODAY.md`，必须先定义 `MINIMUM_USABLE_MODEL` 的口径。

---

## 附录 A：证据产物与哈希

| 产物 | 字节 | SHA-256（前 16 hex） | 时刻 |
|---|---:|---|---|
| `.state/scan/20261019_235838_volume_bracket.json` | 1,794 | `7fbf87609f8b6f3d` | 2026-10-20 00:01:02 本地 |
| `.state/scan/20261019_235838_summary.md` | 5,721 | `0c124aa580d9f070` | 同上 |
| `.state/scan/20261019_235838_dir_errors.jsonl` | 89,751 | `ac7a4bdbe6781913` | 同上 |
| `.state/scan/20261019_235838_buckets.jsonl` | 469,736 | `3b7666e70ce3f79c` | 同上 |
| `.state/candidates/20261019_204847-candidates.jsonl` | 303,784,133 | `a6099d7800f40ad1` | 2026-10-19 20:48:47 本地 |

补充只读复核（2026-10-20 10:50–10:54 本地）：

- `Get-Disk` / `Get-Partition` / `Get-Volume`：分区与卷容量；
- `shutil.disk_usage`：C:/D: 实时已用/可用；
- `os.stat`：pagefile 19,656,269,824 B（18.306 GiB）、swapfile 16,777,216 B、DumpStack 12,288 B；三者 `dev=0, ino=0`；
- `Win32_PageFileUsage`：AllocatedBaseSize 18,745 MB、CurrentUsage 1,772 MB、PeakUsage 2,395 MB；
- `Win32_ComputerSystem`：`AutomaticManagedPagefile=True`，物理内存 16,903,938,048 B；
- `HKLM\...\SystemRestore`：`RPSessionInterval=1`，`DisableSR` 未设置；
- `Get-Service VSS`：Stopped / Manual。

未执行：全盘重扫、24h 增长复查（原定 2026-10-20 19:05）、任何删除/提权采集。

---

## 附录 B：拒绝目录前缀（Top 20，C:）

| 前缀（3 段） | 拒绝数 |
|---|---:|
| `c:/programdata/microsoft/windows` | 412 |
| `c:/programdata/microsoft/windows defender` | 11 |
| `c:/windows/system32` 下分散项 | 19（合计，非单一前缀） |
| `c:/windows/syswow64` 下分散项 | 8（合计） |
| `c:/windows/appcompat` | 5 |
| `c:/programdata/dell` | 5 |
| `c:/windows/serviceprofiles` | 3 |
| `c:/windows/pla` | 3 |
| `c:/windows/security` | 2 |
| `c:/users/<user>/appdata` | 2 |
| `c:/windows/wumodels` | 1 |
| `c:/windows/temp` | 1 |
| `c:/windows/systemtemp` | 1 |
| `c:/windows/servicestate` | 1 |
| `c:/windows/resources` | 1 |
| `c:/windows/provisioning` | 1 |
| `c:/windows/prefetch` | 1 |
| `c:/windows/modemlogs` | 1 |
| `c:/windows/microsoft` | 1 |
| `c:/windows/logs` | 1 |

完整 503 条路径清单只存本地 `.state/scan/20261019_235838_dir_errors.jsonl`，不进入公开仓库。

---

## 附录 C：更正记录

| # | 旧表述 | 实测/更正 | 影响 |
|---|---|---|---|
| 1 | “401 个是 `AppCrash_dwm.exe_*` WER 归档” | 401 个是 WER 相关拒绝（400 个 ReportArchive + 1 个 Temp）；`dwm.exe` 只有 **17** 个；最多的是 `FusionService.exe` 56、`CT01Service.exe` 42 | 稳定性诊断对象扩大；不再把 WER 说成单一 dwm 问题 |
| 2 | 隐藏分区合计 21.17 GiB（1.03 + 18.20 + 1.55 + 0.39） | 二进制 GiB 实际 **19.948 GiB**（1.031 + 16.953 + 1.448 + 0.391 + 0.125 MSR）；旧值把十进制 GB 当 GiB，且漏 MSR | 高估 1.22 GiB；分区层总账已闭合 |
| 3 | “100–230 MB/min ≈ 0.14–0.33 GiB/天” | 正确为 **5.86–13.48 GiB/h（活跃期）**；本次 10.83 h 实测平均 **0.652 GiB/h**；不得线性外推日速率 | 所有清理情景的“持续写入”分量需要重写；旧换算撤回 |
| 4 | “Image 18.20 GiB / DELLSUPPORT 1.55 GiB” | 分别为 **16.953 GiB / 1.448 GiB**（十进制 GB 误标 GiB） | 同上 |
| 5 | “401 个拒绝目录” | C: 501 + D: 2 = **503**；C: 中 WER 401、Defender 11、其他 89 | 与既有 `volume-reconciliation.md` 一致；无变化 |
| 6 | “C: 198.940 GiB / C+D 455.994 GiB” | 原始分区字节 **198.969 GiB**（C:）、**456.023 GiB**（C+D）；旧值系十进制 GB 误标 GiB | 可用空间算术闭合（198.969 − 152.637 = 46.332 ≈ 46.331）；不影响已用/枚举/残余 |

---

## 附录 D：一句话给下一会话

**C: 的 45.102 GiB 仍未解释，但已证明不是 pagefile、不是休眠文件、不是 USN；最可能藏在 VSS/SVI、NTFS 元数据、分配差和 503 个拒绝目录里。下一步不是清理，是拿一次管理员只读采集，把 45.102 GiB 拆开；在此之前，HOLD 不动。**
