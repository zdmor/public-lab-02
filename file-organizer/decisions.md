# 裁决与整改记录（decisions.md）

时间序。评审方为 ChatGPT（Ding Zhai）。本阶段无任何用户清理授权。

## 2026-10-19 PHASE_SCAN_BASELINE（阶段裁决）
清理批次暂缓，转入只读盘点。已执行：安全切换、9 份快照 + journal 全留痕、
同口径 diff、C/D 对账、8 份文档、脱敏包。0 新删除。

## 2026-10-19T15:20Z SCAN_PHASE_REVIEW（维持 HOLD）
三项 P0 + 两项核对。已全部落实（见 `RUN_LOG.md` §11），并在过程中发现并修复
pagefile 16.287 GiB 漏计漏洞。

## 2026-10-20T00:17Z SCAN_R3_REVIEW —— **scan-analysis PARTIAL PASS，无删除授权**
确认 pagefile 修复与括号测量；要求 5 条修订。落实状态：

| # | 要求 | 状态 |
|---|---|---|
| 1 | `CONTENT_NEW_VERIFIED` 过强 → 改名 `PATH_ADDED_RECENT_MTIME`，前者保留给更强证据 | **已改**（`src/tools/snapshot_diff.py` + `file-delta-summary.md` + 重跑 r3） |
| 2 | `net_index_delta` 只作索引算术；`POSSIBLE_MOVED` 是启发式；报告一致使用 | **已保持**（工具硬编码声明 + 文档 §1 界线段） |
| 3 | 501 拒绝目录 `<=3 GiB` 是假设不是上界 → 改 UNKNOWN；WER 归档值得提权后诊断 | **已撤回**（`volume-reconciliation.md` §3.1） |
| 4 | `EVIDENCED_CLEANUP_CEILING` / `POST_CLEANUP_FLOOR` 伪精确 → 改名并计入重装与持续写入 | **已改名** `IDENTIFIED_CANDIDATE_TOTAL` / `POST_SELECTED_CANDIDATES_SCENARIO`（`bridge-ledger.md` §3、`baseline.md` §4） |
| 5 | 授权一次**只读**提权采集是下一个证据节点 | **BLOCKED**，未尝试提权 |

### Single NEXT（按评审原文）
取得运营方授权后跑一次 elevated read-only C: 空间归因，发布脱敏分配对账；
然后评估当前扫描系统是否满足完整性与可复现性标准。
在此之前只做证据缺口收窄与标签校正，不扩架构、不宣称清理目标达成。

## 未授权事项（明确不做）
T1/T2/T3 删除批次、回收站清空、软件卸载、自动快照清理、
任何基于"差额=垃圾"的清理、新定时任务、新架构提案。
