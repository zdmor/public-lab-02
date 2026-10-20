# 新增文件分类报告（PHASE_SCAN_BASELINE，2026-10-20 R3 修订）

> 裁决要求：两份真实快照同口径 diff，区分"新增"与"推断近期"。
> **2026-10-20 R3 修订**：`CONTENT_NEW_VERIFIED` 被评审判定为仍然过强——
> "(旧索引无此路径) AND (mtime ≥ 旧 as_of)" 只能证明**新观察到的路径带着一个
> 近期修改时间戳**，不能证明新内容。已改名为 `PATH_ADDED_RECENT_MTIME`，
> 34.82 GiB 归入"近期 mtime 子组"，不再称作已验证的新建内容。
> `CONTENT_NEW_VERIFIED` 保留为**预留空类**（需内容身份或来源证据，快照两者皆无）。

## 1. 方法与口径

- 输入：`.state/candidates/20261011_180412-candidates.jsonl`（530,337 条）
  与 `20261019_204847-candidates.jsonl`（631,213 条），schema 逐字段一致。
- as_of：10-11 18:04:12 → 10-19 20:48:47（窗口 8.11 天，取自文件名戳）。
- 唯一口径差：IM 客户端数据树 10-19 新冻 ⇒ 其 18,159 条消失项标记
  `PATH_GONE_CALIBER`（7.73 GiB），**不是删除，不计真实删除**。
- 工具：`tools/snapshot_diff.py`（复用 disk_scan 的 bucket_of，桶口径一致）；
  输出 `20261019_235435_snapdiff_1011_to_1019_r2_summary.{json,md}` + detail。

### 分类语义（每类都写明"证明什么 / 不证明什么"）

| 类 | 判定条件 | 证明 | **不证明** |
|---|---|---|---|
| `PATH_ADDED_RECENT_MTIME` | 旧快照无此路径 且 mtime ≥ 旧 as_of | 新观察到的路径带着近期修改时间戳，窗口内有字节写入该路径 | 字节唯一或新建——从扫描根之外拷入/恢复/重下也会得到新 mtime；mtime 可被任意设置。**这是近期 mtime 子组，不是内容身份发现** |
| `PATH_ADDED_BACKDATED_MTIME` | 旧快照无此路径 且 mtime < 旧 as_of | 该路径是旧枚举未记录的新路径 | 内容是新的——mtime 早于旧快照，可能是恢复、保留时间戳的解包、或从旧快照未覆盖之处搬来。**内容年龄未知** |
| `CONTENT_NEW_VERIFIED` | **预留空类，本工具从不输出** | 只有内容身份（st_dev/st_ino）或来源（内容哈希 / USN 创建事件）能支撑；candidates 快照两者皆无，故该计数恒为 0 | — |
| `SIZE_MTIME_CHANGED` | 两快照都有，但 size 或 mtime 变了 | (size, mtime) 元组变了 | 内容变了——touch、零字节追加、元数据重写都会移动 mtime；本工具不计算哈希 |
| `PATH_REMOVED_VERIFIED` | 旧有新无，且不在口径前缀下 | 该路径在新快照中不再可观察 | 已永久删除——可能移到扫描根之外，也可能仍在回收站或影子副本里 |
| `PATH_GONE_CALIBER` | 落在声明的口径前缀下 | 因扫描口径变化而从索引消失 | 任何删除。**已从删除计量中排除** |
| `POSSIBLE_MOVED` | 消失项与新增项的 (size, round(mtime,6)) 相等 | 两路径的 (size, mtime) 匹配 | **它们是同一个文件**。candidates 快照不含文件身份（st_dev/st_ino）也不含内容哈希，(size,mtime) 只是启发式，可能碰撞。**只能报 POSSIBLE，不能报 MOVED** |

### `net_index_delta` 的界线（审计明确要求）

`net_index_delta = 新增 − 真实消失 + size Δ = +34.08 GiB`。
这是**两次枚举之间的索引算术**，**不是物理磁盘增长的度量，不得作为物理增长证据**。
物理增长需要同一窗口配对的卷级 used-bytes 基线——本仓库用
`tools/volume_bracket.py` 做括号式测量（见 volume-reconciliation.md §1），
本次 diff 的 `--volume-baseline` 输出把两个数字并列，但**两者时间戳不同**
（索引 10-19 20:48，括号测量 10-19 23:58），不可直接相减。

## 2. 总量

| 类 | 条目 | 字节 |
|---|---|---|
| `PATH_ADDED_RECENT_MTIME`（近期 mtime 子组，**非已验证新建内容**） | 109,950 | **34.82 GiB** |
| `PATH_ADDED_BACKDATED_MTIME`（mtime 回填，内容年龄未知） | 41,688 | **11.37 GiB** |
| `CONTENT_NEW_VERIFIED`（预留空类） | 0 | 0 |
| 新增小计 | 151,638 | 46.19 GiB |
| `SIZE_MTIME_CHANGED` | 16,626 | 净 −1.71 GiB（其中 pagefile 一项 −2.11） |
| `PATH_REMOVED_VERIFIED` | 32,603 | 10.40 GiB |
| `PATH_GONE_CALIBER`（IM 客户端口径） | 18,159 | 7.73 GiB |
| `POSSIBLE_MOVED`（启发式） | 12,768 | 2.15 GiB |
| UNCHANGED | 450,181 | — |
| 非新增但 mtime 近期：young_unchanged | 32,396 | 8.46 GiB |
| 非新增但 mtime 近期：modified_in_window | 16,461 | 21.04 GiB |
| **净索引增量（算术，非物理增长）** | | **+34.08 GiB** |

交叉验证：与 earliest-history-evidence.md §2 的全集趋势
（136.97 − 7.73 = 129.24 → 163.31，即 +34.07 GiB）咬合，误差 0.01 GiB。
两者都是索引口径，因此互相印证**只说明索引自洽**，不说明物理盘增长。

## 3. PATH_ADDED_RECENT_MTIME 按桶（top 10，34.82 GiB）

| 桶 | 文件 | GiB | 主要扩展 |
|---|---|---|---|
| C:/users | 54,788 | 9.19 | exe:2.4, dll:1.4, dat:0.7 |
| C:/program files (x86) | 6,638 | 7.04 | dll:3.7, pak:1.3, data:1.1 |
| D:/dataarea | 8,900 | 4.55 | exe:1.4, dll:0.7, json:0.6 |
| D:/baidunetdiskdownload | 69 | 4.09 | exe:2.4, zip:1.4, msi:0.2 |
| D:/downloads | 13 | 2.63 | exe:2.4, msix:0.1, crdownload:0.1 |
| D:/program files (x86) | 4,968 | 2.57 | dll:1.1, exe:0.7, asar:0.2 |
| D:/program files | 754 | 1.93 | dll:0.6, asar:0.5, exe:0.4 |
| C:/program files | 3,540 | 1.04 | dll:0.6, exe:0.2, pak:0.1 |
| D:/ai_labs | 1,342 | 0.71 | exe:0.5, pak:0.1, dll:0.1 |
| C:/programdata | 369 | 0.41 | vdm:0.2, dll:0.1, etl:0.1 |

按卷：C: 17.84 GiB（65,585 文件）、D: 16.98 GiB（44,365 文件）。
按 mtime 年龄：0–7d 32.77 GiB（99,240 文件）、7–30d 2.05 GiB（10,710 文件）。

## 4. PATH_ADDED_BACKDATED_MTIME 按桶（top 8，11.37 GiB —— **内容年龄未知**）

| 桶 | 文件 | GiB | 主要扩展 |
|---|---|---|---|
| D:/program files | 17,432 | 5.10 | exe:1.4, dll:0.8, l:0.7 |
| C:/users | 12,688 | 2.18 | exe:1.3, dll:0.3, js:0.1 |
| D:/dataarea | 6,668 | 1.31 | dll:0.6, exe:0.5, pak:0.0 |
| D:/program files (x86) | 1,023 | 1.04 | exe:0.6, asar:0.3, dll:0.1 |
| C:/program files (x86) | 917 | 0.86 | dll:0.5, data:0.2, pak:0.2 |
| D:/data | 809 | 0.75 | pdf:0.4, exe:0.1, docx:0.1 |
| D:/keil_packs | 1,869 | 0.13 | pdf, pdsc, chm |
| C:/windows | 17 | 0.01 | dll, sys, cat |

按卷：D: 8.33 GiB（28,066 文件）、C: 3.04 GiB（13,622 文件）。
形态高度吻合"安装器解包 / 目录迁移保留时间戳"：D:/program files 一个桶就占 5.10 GiB
且扩展名是 exe/dll。**这一组不能计入"这八天新产生的数据"。**

## 5. 归因叙述：34.82 GiB 近期 mtime 子组的窗口内写入是谁干的

1. **用户 profile（C:/users 9.19 GiB / 54,788 文件）**：最大单一来源。
   AppData\Local 新鲜部分：WPS 1.18、Chrome 0.74、Edge 0.62、MS Teams 0.47、
   Claude VM bundles ~1.2、pip cache 0.37、**各类 updater 安装文件与 pending
   ~1.6 GiB**（raccoon-ai 0.44 + cherrystudio 0.37+0.29 + deepseek 0.27 +
   nanomuse 0.25，均为可再下载件）；另有微信文件 0.98 GiB（真实用户数据，不清）。
2. **应用安装/自动更新 churn（C: Program Files 系 ~8.1 + D: Program Files 系
   ~4.5 GiB 窗口内写入）**：Edge 1.73 + Copilot 1.70 + EdgeWebView 1.69 +
   EdgeCore 1.38（C: 侧四项共 ~6.5 GiB）；IM 客户端升级到 5.0.12.6018（0.82 GiB）；
   D: 侧 Cherry Studio / raccoon-ai / Cursor / 百度网盘 module 资源包。
3. **工具链下载（D: 下载区 4.09 + 2.63 GiB）**：百度网盘下载目录新增
   野火 STM32 实战资料之"4-软件工具"子目录 2.84 GiB / 21 文件（Keil 等安装文件集合）；
   D:\Downloads 2.63 GiB。
4. **工程数据落盘（D:/dataarea 4.55 GiB）**：C-Link 下腾讯系 AppData
   迁移副本；WorkBuddySpace 下 .workbuddy 数据 1.21 + downloads 1.05。
5. **新工具目录**：AI_Labs（10-15 创建，0.71）、Keil_Packs（10-16 创建）。

## 6. 反向：PATH_REMOVED_VERIFIED（10.40 GiB / 32,603 条）

C:/users 4.08（dll 0.9 为主，应用更新替换）、C:/program files (x86) 3.02、
C:/program files 1.03、D:/program files (x86) 0.82、D:/program files 0.54、
D:/dataarea 0.48、C:/programdata 0.40、C:/windows 0.02。
形态为应用更新替换 + profile 缓存轮换，**无用户数据整树消失**。
本窗口内本系统可证明的自有删除（purge 1,985.6 MiB + housekeep 净 1.331 GB）
发生在 10/19 白天，已含于上表分布中。

## 7. 噪声项说明（不计入增长叙事）

- `C:\pagefile.sys`：18.41 → 16.29 GiB（托管伸缩，−2.11 GiB），
  是 SIZE_MTIME_CHANGED 净额为负的主因。**它同时证明"索引差额"会被
  系统托管文件干扰**：这一项是真实的大小变化，不是删除。
- `C:\swapfile.sys`：体积不变、mtime 移动，正常换页活动。
- `POSSIBLE_MOVED` 12,768 条 / 2.15 GiB：**(size, mtime) 配对的启发式结果**，
  不是文件身份比对。字节在卷内搬运，不构成增减；配对可能误命中。

## 8. 一句话结论

**磁盘不是被某个大文件填满的**，索引口径下窗口内写入 34.82 GiB，
来自应用自动更新（Edge/Copilot/IM 客户端等约 10 GiB）、AI 工具链下载与安装
（约 8 GiB）、用户 profile 中可再生成的缓存与 updater 残件（约 3 GiB）、
工程数据落盘（约 6 GiB）。另有 11.37 GiB 是**内容年龄未知的回填 mtime 路径**，
不能算作新数据。可识别、可再生的部分集中在 cleanup-candidates.md；
真实用户数据（微信文件、PRJ3_EXAM 数据等）不清。

## 9. 2026-10-20 R3 评审修订记录

`SCAN_R3_REVIEW_20261020.md`（GitHub example-owner/public-lab-02，00:17Z）裁定
scan-analysis **PARTIAL PASS**、无删除授权，并要求下列修订。本文件已全部落实：

| R3 条目 | 原文要求 | 落实 |
|---|---|---|
| 1 | `CONTENT_NEW_VERIFIED` 仍然过强，"(旧索引无路径) AND (mtime ≥ 旧 as_of)" 只能证明新观察到的路径带近期 mtime；改名 `PATH_ADDED_RECENT_MTIME` 或等价，`CONTENT_NEW_VERIFIED` 保留给更强证据；34.82 GiB 放近期 mtime 子组 | `tools/snapshot_diff.py` 已改名并重跑（`20261020_082536_snapdiff_1011_to_1019_r3`）；`CONTENT_NEW_VERIFIED` 进 CLASS_META 标 RESERVED 且**从不输出**（计数恒 0）；本文件 §1/§2/§3/§4/§5 同步 |
| 2 | `net_index_delta` +34.08 GiB 只是索引算术，绝不描述为物理净增长；`POSSIBLE_MOVED` 是启发式；报告须一致使用这些限定 | §1 的"界线"段保留；`tools/snapshot_diff.py` 输出仍硬编码该声明；§8 一句话结论改为"索引口径下" |
| 3 | 501 拒绝目录的 `<=3 GiB` 是假设不是可辩护上界；记 UNKNOWN 或附条件粗略估计；401 个 WER 归档本身值得提权后类别级诊断 | `docs/volume-reconciliation.md` 新增 §3.1 撤回该上界，改 **UNKNOWN**，只保留"不太可能是主体"的弱判断；WER 崩溃诊断已列为待办 |
| 4 | `EVIDENCED_CLEANUP_CEILING` 不是绝对上限；`POST_CLEANUP_FLOOR` 不是内在底线；改名并计入重装与持续写入 | 改名 `IDENTIFIED_CANDIDATE_TOTAL` / `POST_SELECTED_CANDIDATES_SCENARIO`（见 `docs/bridge-ledger.md` §3、`docs/baseline.md` §4），并明确列出未计的三项：重装回归、持续写入 0.14–0.33 GiB/天、新候选 |
| 5 | 本机非管理员；授权下一次**只读**提权采集是下一个证据节点 | 提权拒绝证据已固化；本轮不尝试提权，等授权 |

Single NEXT（按评审）：取得运营方授权后跑一次 elevated read-only C: 空间归因，
发布脱敏分配对账；在此之前只做证据缺口收窄与标签校正，不扩架构、不宣称清理目标达成。
