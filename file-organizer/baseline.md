# 基线：全卷同时点空间盘点（PHASE_SCAN_BASELINE）

> 阶段裁决 `PHASE_SCAN_BASELINE_DECISION.md` 要求：清理批次暂缓，转入只读盘点。
> 本文件是**同时点**基线与三套基线口径的区分。所有删除类动作在本阶段不执行。
> 测量时间：2026-10-19 20:46–21:20（本地，UTC+8）。机器持续并发写盘
> （实测 100–230 MB/min），每个数字都带采集时刻，跨表比较前先对时。

## 1. 卷级测量（Win32_LogicalDisk，21:14 本地）

| 卷 | 总容量 | 已用 | 可用 | 文件系统 | 卷序列号 |
|---|---|---|---|---|---|
| C: | 198.94 GiB | 152.55 GiB | 46.41 GiB | NTFS | 028C45D5X |
| D: | 257.05 GiB | 116.98 GiB | 140.07 GiB | NTFS | 9C19FF19X |

同时点括号测量（2026-10-19 23:58:38–00:01:02 本地，drift ≤0.73 MiB，
**优先采用此组**）：C: used_mid **152.637** GiB / D: used_mid **117.091** GiB，
合计 269.728 GiB。产物 `.state/scan/20261019_235838_volume_bracket.json`。

附加事实（同次采集）：

- 活动页面文件：`C:\pagefile.sys`，WMI 分配 16,677 MB（13:00Z 采样）；
  快照 stat 实测 17,487,994,880 B = 16.29 GiB（10/19 20:48）。
- 休眠文件：**不存在**。`powercfg /a` 输出"休眠尚未启用"；
  `Get-Item C:\hiberfil.sys` 返回 ItemNotFound（非访问拒绝）。
- `AutomaticManagedPagefile = True`（页面文件由系统托管，体积会自行伸缩）。
- 隐藏分区（无盘符，不计入 C:/D: 已用）：1.03 GiB、Image 18.20 GiB、
  DELLSUPPORT 1.55 GiB、ESP 0.39 GiB（Win32_Volume 枚举）。

## 2. 文件枚举（tools/disk_scan.py，20:46:27 本地 = 12:46:27Z）

| 卷 | 文件数 | 字节数 | reparse 点（不展开） | 硬链接重复命中 | 目录拒绝 | 无身份文件 |
|---|---|---|---|---|---|---|
| C: | 694,708 | 107.535 GiB | 108 | 68,411 | 501 | 3 |
| D: | 486,534 | 116.333 GiB | 1,331 | 28,404 | 2 | 0 |
| 合计 | 1,181,242 | 223.868 GiB | 1,439 | 96,815 | 503 | 3 |

（2026-10-20 修订：C: 字节数由 91.09 → **107.535 GiB**，原因是修复了
`(st_dev, st_ino) == (0,0)` 导致 pagefile/swapfile 被当成硬链接漏计的 bug，
详见 volume-reconciliation.md §4。上表的 20:46 数字已被 23:58 括号测量取代。）

方法学要点：reparse 点记录但不展开（避免经 junction 双算）；
硬链接按 (st_dev, st_ino) 去重（`DirEntry.stat()` 在 Windows 上
st_dev/st_ino 恒为 0，必须用 `os.stat(path)` 取真实身份）；
长路径经 `\\?\` 前缀遍历；目录权限错误逐目录计数不致命。
~~**系统保留路径（pagefile/swapfile/SVI）在本枚举中计 0 字节**（`\\?\` 前缀下
stat 返回空）~~ —— **2026-10-20 更正**：不是"计 0"，而是 `os.stat()` 对
pagefile/swapfile 返回 `st_dev=0, st_ino=0`，被去重逻辑误判为硬链接而
**漏计**（已修，见 volume-reconciliation.md §4）。修复后 pagefile 16.287 GiB
与 swapfile 0.016 GiB 正常计入枚举，各计一次。
SVI 仍因访问拒绝计 0（2 个目录，计入覆盖率指标）。

## 3. 受管全集（gov candidates，过滤口径，20:48 本地）

- 条目 631,213，合计 163.31 GiB（`.state/candidates/20261019_204847-candidates.jsonl`）。
- 过滤口径排除：Windows/WinSxS、两个回收站、`_restore`/`_cleanup_scripts`、
  IM 客户端数据树（2026-10-19 新冻）、云盘挂载点。
- 与 10/11 同口径快照的 diff 见 file-delta-summary.md。

## 4. 三套基线口径（事实 vs 估计，按裁决要求区分）

| 基线 | 定义 | 当前最佳值 | 性质 |
|---|---|---|---|
| EARLIEST_PROVEN | 有快照证据的最早可枚举状态 | 2026-09-22：555,110 条目 / 140.76 GiB（过滤口径，彼时不含用户文档目录） | **事实**（快照 + sha256 见 manifest） |
| CLEAN_OS_ESTIMATE | 同一套软件装干净后的占用估计 | C: 60–80 GiB（Windows 子树约 28 + 页面文件 16.3 + Program Files 约 6 + 驱动/Office/运行时约 15–30） | **估计**：无 2025-09-26 装机时刻的快照，无法证明；标注 UNKNOWN 项见 earliest-history-evidence.md |
| ~~LEAN_WORKSTATION_ESTIMATE~~ | ~~精简工作站应占用 130–150 GiB~~ | **已撤回（WITHDRAWN，2026-10-20）** | 该值是从"当前已用减掉我以为能清的东西"反推出来的，不是任何一次测量的结果。逐桶桥接后缺口 80–100 GiB 在账本里没有对应项。**见 bridge-ledger.md** |
| IDENTIFIED_CANDIDATE_TOTAL（替代，2026-10-20 R3 改名） | **当前已识别**条件候选之和，非上限 | **39.2 GiB**（A+B 3.5 + C 6.7 + D 18.5 + E 8.0 + F 2.5），全部需条件 | **实测桶求和**；未识别候选仍可能藏在 45.102 GiB UNKNOWN 残余 / 503 个未测目录 / WinSxS 里 |
| POST_SELECTED_CANDIDATES_SCENARIO（替代，2026-10-20 R3 改名） | A–F 全删且无再生的 what-if 小计 | **230.5 GiB**（269.728 − 39.2） | **算术情景**，非稳态底线；未计重装回归、持续写入（0.14–0.33 GiB/天）、新候选 |
| UNSUBSTANTIATED_TARGET（重标） | 130–150 GiB | 保留为"未证实目标"，附缺口说明 | **估计**：账本缺口 80–100 GiB 无证据来源 |

## 5. 本阶段 KPI（截至阶段冻结 12:37:58Z 的可证明数字）

阶段冻结前已完成的可证明清理（全部留痕于 purge journal 1879 行）：

| 批次 | 净释放 | 文件数 | 失败 | 说明 |
|---|---|---|---|---|
| T1 缓存/暂存 | 1,985.6 MiB | 6,604 | 6（OS 锁定组件 DLL） | 白名单正向 + 冻结根 + 预算硬顶 |
| housekeep v2 净额 | 1.331 GB | — | 0 | raw−gz 净口径，卷 free 前后吻合 |
| 调度自然跑净额 | 396.7 MB | — | 0 | 卷 delta +396.7 MB 独立吻合 |

阶段冻结后（12:37:58Z 起）：**0 字节新删除**。本阶段产出为盘点与解释，不是 GB 数。

## 6. 证据链

- 卷测量：`Win32_LogicalDisk` / `Win32_Volume` / `Win32_PageFileUsage` /
  `Win32_OperatingSystem` / `powercfg /a`（本文件 §1 同时采集）。
- 枚举：`.state/scan/20261019_204257_buckets.jsonl` +
  `20261019_204257_summary.md`（90 桶全量 + top40 + 时段 + 扩展名 rollup）。
- 快照与 manifest：`.state/candidates/*.jsonl[.manifest.json]`（9 份，
  sha256 见各自 manifest 的 `manifest_hash` 字段）。
- diff：`.state/scan/20261019_210025_snapdiff_1011_to_1019_summary.{json,md}` +
  `_detail.jsonl`（工具 tools/snapshot_diff.py）。
