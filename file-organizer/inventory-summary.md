# 盘点摘要：全卷占用结构（PHASE_SCAN_BASELINE）

> 由 `docs/app-space-top.md`（分类 Top）与 `docs/age-cohorts.md`
> （时间队列）合并而成。数字来自 2026-10-19 23:58 括号式同时点枚举
> （`.state/scan/20261019_235838_buckets.jsonl`）。硬链接按卷内
> (st_dev, st_ino) 去重，跨路径不重复计量。

---

## 第一部分：应用/目录空间 Top

# Top30 空间占用归属（PHASE_SCAN_BASELINE）

> 数据源：disk_scan 20:46 本地同时点枚举（90 桶取 top 30）。
> 分类列用于回答"这 GB 是谁的、能不能动"：用户数据 / 应用 / 系统 /
> 可再生缓存 / 迁移暂存 / 回收待裁 / 本系统证据。
> **本表不构成删除清单**——处置见 cleanup-candidates.md 且本阶段冻结。

| # | 桶 | 卷 | GiB | 文件 | 分类 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 我的坚果云 | D: | 19.31 | 16,712 | 用户数据 | 同步盘主体（zip 15.7）；云挂载点已排除在扫描外 |
| 2 | Windows/WinSxS | C: | 16.60 | 123,489 | 系统 | 组件存储；可回收量需提权 DISM（UNKNOWN） |
| 3 | D:/Program Files | D: | 16.19 | 59,589 | 应用 | Keil/百度网盘/Cursor/Cherry 等 |
| 4 | D:/Program Files (x86) | D: | 15.53 | 65,434 | 应用 | IM 客户端/EdgeWebView 等 |
| 5 | WorkBuddySpace | D: | 10.92 | 113,124 | 工具运行时 | log 3.2G + json 2.2G + exe 1.1G；日志可治理 |
| 6 | D: 回收站 | D: | 9.10 | 3,835 | 回收待裁 | ndjson 7.0G（本系统旧 journal）；30 天内删除 |
| 7 | IM 客户端数据 | D: | 7.91 | 24,099 | 应用数据 | **已冻结**（GO 指令：不得凭 Cache 判定可再生） |
| 8 | Users/Documents | C: | 7.77 | 25,911 | 用户数据 | dat 2.2G + enc 0.9G；微信文件在增长 |
| 9 | C-Link | D: | 7.24 | 38,208 | 迁移暂存 | dll 2.5G + exe 1.8G + xlog 0.8G |
| 10 | new_tdx64 | C: | 6.52 | 31,135 | 行情数据 | lc1 4.8G（通达信本地数据） |
| 11 | Windows/System32 | C: | 5.16 | 6,710 | 系统 | 不动 |
| 12 | AppData/Local/Microsoft | C: | 4.62 | 39,732 | 应用数据 | ort 0.3G + dmp 0.2G（转储文件可清） |
| 13 | _Trash/2026-09-10/qianfan | D: | 4.34 | 26,795 | 迁移垃圾 | zst 1.9G；历史清理遗留，候选处置 |
| 14 | C: 回收站 | C: | 4.32 | 24,376 | 回收待裁 | exe 1.9G + body 0.5G |
| 15 | BaiduNetdiskDownload | D: | 4.12 | 115 | 下载暂存 | 野火 STM32 资料 2.84G 在窗口内新增 |
| 16 | Projects | D: | 3.46 | 64,891 | 工程数据 | db/csv/gz；用户资产 |
| 17 | AppData/Local/Qianwen | C: | 3.23 | 68,802 | 应用 | 本地模型/运行时（pyd 0.6G） |
| 18 | AppData/Local/Kingsoft | C: | 2.83 | 13,692 | 应用 | WPS 本地数据 |
| 19 | Downloads | D: | 2.64 | 16 | 下载暂存 | exe 2.5G / 13 文件在窗口内新增 |
| 20 | AppData/Roaming/kingsoft | C: | 2.64 | 46,805 | 应用 | WPS 配置/缓存 |
| 21 | file-organizer/.state | D: | 2.57 | 238 | 本系统证据 | jsonl 1.1G + ndjson 1.0G；**保留**（审计链） |
| 22 | Program Files (x86)/Microsoft | C: | 2.54 | 2,221 | 应用 | dll 1.5G |
| 23 | WPS Software | D: | 2.50 | 1,155 | 应用 | pdf 0.9G + tif 0.7G（含授权/模板资产） |
| 24 | Windows/Panther | C: | 2.48 | 131 | 系统 | 安装/更新缓存；log 2.5G |
| 25 | .workbuddy_bak | C: | 2.46 | 58,298 | 备份 | 旧版备份；确认新版本健康后可议 |
| 26 | BookKnowledge | D: | 2.17 | 2,881 | 用户数据 | 书库（pdf/epub 各 0.9G） |
| 27 | AppData/Local/Packages | C: | 2.14 | 13,049 | 应用数据 | UWP（Teams/Claude 等） |
| 28 | .box-agent | C: | 2.03 | 40,557 | 工具 | 自动化代理；browsers 0.66G 窗口内新增 |
| 29 | $WinREAgent | C: | 1.96 | 9 | 系统 | 恢复环境；wim 2.0G，勿动 |
| 30 | AI_Labs | D: | 1.12 | 24,038 | 新工具 | 2026-10-15 创建；窗口内 +0.71 GiB |

## 分类汇总（top 30 口径）

| 分类 | GiB | 说明 |
|---|---|---|
| 用户数据（坚果云/Documents/Projects/BookKnowledge） | ≈32.7 | 不清 |
| 应用 + 应用数据 | ≈88.6 | 按卸载/缓存治理，需业务确认 |
| 系统（WinSxS/System32/Panther/WinREAgent） | ≈26.2 | 需提权才知可回收量 |
| 迁移暂存 + 迁移垃圾（C-Link/_Trash） | ≈11.6 | 架构裁决项 |
| 回收待裁（C:+D: 回收站） | ≈13.4 | 用户一次决策 |
| 下载暂存 | ≈6.8 | 安装文件集合，多数可再下载 |
| 本系统证据（.state） | 2.57 | 保留 |

> 全卷 90 桶完整数据：`.state/scan/20261019_204257_buckets.jsonl`。
> 硬链接已去重（96,817 次重复命中未计）；reparse 点 1,439 个未展开。


---

## 第二部分：mtime / ctime 时间队列

# 时间维度分析（PHASE_SCAN_BASELINE）

> 数据源：disk_scan 同时点枚举（20:46 本地）+ 快照 diff（10-11→10-19）。
> mtime 是"最后写入"而非"创建"：安装器解压、备份还原会保留旧时间戳，
> 因此老时段里也可能藏着新落盘的内容（见 §3）。

## 1. 全卷 mtime 时段分布（GiB，磁盘扫描 20:46 本地）

| 时段 | GiB | 占比 | 解读 |
|---|---|---|---|
| 0-7d | 39.70 | 19.2% | 活跃工作集：本窗口的新增+改动全落这里 |
| 7-30d | 53.09 | 25.6% | 近一个月沉淀：安装器、下载、日志轮换 |
| 30-90d | 59.66 | 28.8% | 深层档案：主体数据与旧安装 |
| 90-180d | 22.78 | 11.0% | 季度层 |
| 180-365d | 4.38 | 2.1% | 半年层（薄） |
| >365d | 27.34 | 13.2% | 一年以上：跨 2025-09-26 装机代的遗留（D: 2023 起内容为主） |
| 合计 | 206.95 | 100% | |

形态解读：双峰（7-30d 与 30-90d）+ 一个老的 >365d 台地。
中间 180-365d 很薄说明 2026 年上半年有一次大规模换血/清理，
与 D: 早于 C: 装机的历史一致。

## 2. VERIFIED_ADDED 的时段分布（vs 10-19 快照，diff 产物）

| 时段 | 文件 | GiB |
|---|---|---|
| 0-7d | 99,240 | 32.77 |
| 7-30d | 23,551 | 6.83 |
| 30-90d | 2,621 | 1.17 |
| 90-180d | 1,513 | 0.62 |
| 180-365d | 268 | 0.28 |
| >365d | 24,445 | 4.51 |

46.19 GiB 新增中 34.82 GiB 的 mtime 落在 8.11 天窗口内（新鲜）；
11.37 GiB 为回填时间戳（见下）。

## 3. 回填时间戳的新增（11.37 GiB / 41,688 文件）——老时段里的新落盘

按 4 段路径聚合 top：

| 路径 | 文件 | GiB | 性质 |
|---|---|---|---|
| D:/program files/keil_v5/arm | 7,100 | 2.91 | Keil 支持包安装器展开（时间戳保留） |
| D:/dataarea/c-link/appdata-roaming-tencent | 6,655 | 0.90 | 迁移副本 |
| C:/program files (x86)/microsoft/edgewebview | 915 | 0.85 | 运行时安装 |
| C:/users/example/appdata | 4,022 | 0.78 | 配置/数据拷贝 |
| D:/program files (x86)/cherry studio/resources | 559 | 0.66 | 应用资源 |
| D:/program files/raccoon-ai/resources | 8,064 | 0.56 | 应用资源 |
| C:/users/example/.box-agent | 8,491 | 0.55 | 工具运行时 |
| D:/data/active/PRJ3_EXAM 数据 | 573 | 0.53 | 用户数据拷入 |
| C:/users/example/.codex | 33 | 0.43 | 插件包 |
| D:/program files/baidunetdisk/module | 655 | 0.42 | 网盘模块 |
| D:/dataarea/c-drive/appdata-local-openai | 4 | 0.40 | 迁移副本 |
| D:/program files/cursor/resources | 1,357 | 0.39 | 应用资源 |

**含义**：用 mtime 判断"新旧"会把这类内容误判为老文件。两份快照的
path-keyed diff 才能证伪——这正是本阶段坚持双快照 diff 的原因。

## 4. INFERRED_RECENT（证不了新增，但 mtime 说近期活跃）

| 子类 | 文件 | GiB | 定义 |
|---|---|---|---|
| young_unchanged | 32,396 | 8.46 | 旧快照时已 ≤7 天，且内容至今未变 |
| modified_in_window | 16,461 | 21.04 | 窗口内 mtime 移动过（含 pagefile/swapfile 等系统噪声） |

这两类是**单快照 mtime 启发式**的残留适用范围：有双快照后，
真正的新增已全部升级为 VERIFIED_ADDED，INFERRED_RECENT 只描述
"被反复读写但路径未变"的热数据（日志、数据库、缓存、换页文件）。

## 5. 主要桶的时段剖面（top 10，GiB by band）

| 桶 | 0-7d | 7-30d | 30-90d | 90-180d | 180-365d | >365d |
|---|---|---|---|---|---|---|
| 我的坚果云 | 0.0 | 15.7 | 0.6 | 0.2 | 0.8 | 2.1 |
| Windows/WinSxS | 0.0 | 0.0 | 10.2 | 1.1 | 1.1 | 4.3 |
| D:/Program Files | 0.9 | 6.3 | 2.0 | 0.4 | 0.7 | 5.9 |
| D:/Program Files (x86) | 3.2 | 4.1 | 2.7 | 2.5 | 0.2 | 2.9 |
| WorkBuddySpace | 2.5 | 3.4 | 4.4 | 0.7 | 0.0 | 0.0 |
| D: 回收站 | 2.0 | 6.9 | 0.2 | 0.0 | 0.0 | 0.0 |
| IM 客户端数据 | 0.9 | 0.0 | 0.3 | 6.7 | 0.0 | 0.0 |
| Users/Documents | 1.1 | 1.6 | 4.9 | 0.1 | 0.0 | 0.1 |
| C-Link | 2.8 | 2.3 | 1.6 | 0.0 | 0.1 | 0.4 |
| new_tdx64 | 0.0 | 0.0 | 6.5 | 0.0 | 0.0 | 0.0 |

观察：
- **回收站 9.10 GiB 几乎全在 30 天内**（7-30d 6.9 + 0-7d 2.0）——删除动作
  集中在本月，与治理工作本身吻合；它是"最近发生的删除"，不是老垃圾。
- **WinSxS 主体 30-90d**：系统更新换代痕迹，可回收量需提权 DISM 才能定量。
- **new_tdx64 6.52 GiB 全在 30-90d**：行情数据一次性落盘后未动。
- **IM 客户端数据 6.7 GiB 在 90-180d**：老视频缓存（已按 GO 指令冻结）。
