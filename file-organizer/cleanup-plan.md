# 可清理分析：候选桶清单（PHASE_SCAN_BASELINE，**暂不删除**）

> 裁决要求：15–30 个桶，只做分析。本阶段一切删除类动作冻结
> （`.state/PHASE_SCAN_BASELINE` 生效中，housekeep `--approve` 被拒 rc=4）。
> 每个桶给：位置 / 当前体量 / 可再生性 / 风险 / 前置条件。
> 体量为 20:46 本地同时点枚举或 10-11→10-19 窗口 diff 的实测值。

## A. 可再生缓存（低风险，应用可自行重建）

| # | 桶 | 体量 | 可再生性 | 风险 | 前置条件 |
|---|---|---|---|---|---|
| A1 | Chrome/Edge 缓存（窗口内新增部分） | 1.36 GiB（Chrome 0.74 + Edge 0.62） | 完全可再生 | 低：仅缓存，不动登录态 | 关闭浏览器或接受静默重建 |
| A2 | pip cache | 0.37 GiB | `pip cache purge` 可再生 | 低 | 无 |
| A3 | .pnpm-store | 0.15 GiB | `pnpm store prune` 可再生 | 低 | 确认无进行中的安装 |
| A4 | npm _cacache | <0.01 GiB | `npm cache clean --force` | 低 | 无 |
| A5 | WER ReportQueue/Temp（崩溃转储） | 未单独量化（含于 AppData/Local/Microsoft dmp 0.2G） | 可再生 | 低 | 无 |
| A6 | Claude-3p partial（下载中断残片） | 0.48 GiB（窗口内新增） | 可再下载 | 低 | 确认无进行中的下载 |

## B. updater 安装文件与 pending（低风险，可再下载）

| # | 桶 | 体量 | 可再生性 | 风险 | 前置条件 |
|---|---|---|---|---|---|
| B1 | raccoon-ai-updater\installer.exe | 0.44 GiB | 可再下载 | 低 | 确认无正在进行的升级 |
| B2 | cherrystudio-updater（installer 0.37 + pending 0.29） | 0.66 GiB | 可再下载 | 低 | 同上 |
| B3 | deepseek-aidsh-desktop-updater\pending | 0.27 GiB | 可再下载 | 低 | 同上 |
| B4 | nanomuse-desktop-updater\installer.exe | 0.25 GiB | 可再下载 | 低 | 同上 |

B 组合计 ≈1.6 GiB，是"看起来像安装失败现场"的典型残件。

## C. 下载暂存（中风险，需用户确认已安装/已使用）

| # | 桶 | 体量 | 可再生性 | 风险 | 前置条件 |
|---|---|---|---|---|---|
| C1 | D:\Downloads | 2.63 GiB / 13 文件 | 多数为安装文件，可再下载 | 中：需逐文件确认 | 用户过清单 |
| C2 | BaiduNetdiskDownload（野火 STM32 资料之软件工具子目录） | 2.84 GiB / 21 文件 | 网盘可重下 | 中 | 确认 Keil 等已装完 |
| C3 | BaiduNetdiskDownload 其余 | ≈1.25 GiB | 网盘可重下 | 中 | 同上 |

## D. 回收站与迁移垃圾（**待用户一次裁决**，阶段冻结中）

| # | 桶 | 体量 | 说明 | 前置条件 |
|---|---|---|---|---|
| D1 | D: 回收站 | 9.10 GiB / 3,835 文件 | ndjson 7.0G 主要是本系统旧 journal | 用户明确"清空回收站"指令 |
| D2 | C: 回收站 | 4.32 GiB / 24,376 文件 | exe 1.9G + body 0.5G | 同上 |
| D3 | _Trash/2026-09-10/qianfan-desktop-app | 4.34 GiB / 26,795 文件 | 历史清理遗留（zst 1.9G） | 用户确认无恢复需求 |
| D4 | desktop-triage-trash | 0.75 GiB / 810 文件 | 本系统桌面分拣的 C 端留档 | 双端已审计（91 VERIFIED_PURGEABLE 留痕） |

## E. 迁移暂存（架构裁决项）

| # | 桶 | 体量 | 说明 | 前置条件 |
|---|---|---|---|---|
| E1 | C-Link | 7.24 GiB / 38,208 文件 | dll 2.5G + exe 1.8G + xlog 0.8G；C 盘软链接迁移副本 | 确认 C: 原路径健康后可整体退役 |
| E2 | C-Drive | 0.73 GiB / 4,704 文件 | 同类迁移副本 | 同上 |
| E3 | _restore / _cleanup_scripts | 未量化 | 已冻结（frozen_prefixes） | **不动**（迁移证据+权威计划脚本） |

## F. 备份与旧版（需确认新版本健康）

| # | 桶 | 体量 | 说明 | 前置条件 |
|---|---|---|---|---|
| F1 | .workbuddy_bak | 2.46 GiB / 58,298 文件 | 旧版备份 | 现版连续运行无故障 N 天 |
| F2 | 多版本共存的 EdgeWebView / IM 客户端旧版 | 未逐版本量化 | 更新替换残留 | 应用自身卸载器处理 |

## G. 系统项（需提权才能定量，本阶段 UNKNOWN）

| # | 桶 | 体量 | 说明 | 前置条件 |
|---|---|---|---|---|
| G1 | WinSxS 可回收量 | **UNKNOWN** | DISM AnalyzeComponentStore 需提权（错误 740） | 一次提权采集 |
| G2 | SVI/VSS 影子副本 | **UNKNOWN** | 访问拒绝；C: 残余 ~45 GiB 的主要嫌疑 | `vssadmin list shadowstorage`（提权） |
| G3 | pagefile.sys | 16.29 GiB | 托管中；**不建议动** | 改系统设置，收益一次性且影响性能 |

## H. 本系统自有（只读阶段保留）

| # | 桶 | 体量 | 说明 |
|---|---|---|---|
| H1 | housekeep 计划：10-03 快照 gzip 压缩 | 0.198 GB 净 | 计划已生成（actions=1）；阶段冻结不执行，解冻后随日常调度跑 |
| H2 | .state 全量 | 2.57 GiB | 审计证据链，保留 |

## 汇总（ESTIMATE，非承诺）

| 组 | 可识别体量 |
|---|---|
| A+B（可再生+updater 残件） | ≈3.5 GiB |
| C（下载暂存，需确认） | ≈6.7 GiB |
| D（回收站+迁移垃圾，待裁决） | ≈18.5 GiB |
| E（迁移暂存，架构裁决） | ≈8.0 GiB |
| F（备份，待确认） | ≈2.5 GiB |
| G（系统，UNKNOWN） | 不可计 |
| **合计（A–F）** | **≈39 GiB** |

与 LEAN_WORKSTATION_ESTIMATE（130–150 GiB）的口径关系：
当前已用 269.5 GiB − 本表 ≈39 GiB − 真实用户数据增长（不清）≈ 落在估计区间内。
**本表没有任何一行在本阶段执行**；执行需用户按组或逐项的一次批量审批。
