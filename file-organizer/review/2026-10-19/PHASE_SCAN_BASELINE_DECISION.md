# File Organizer 阶段裁决：先完善全盘盘点与历史差异，不执行新的清理

状态：PHASE_SCAN_AND_BASELINE / 2026-10-19 / 用户最新决策优先于此前 T1-T3 清理建议。

## 0. 即刻切换执行边界

- 本阶段只做全盘只读盘点、空间对账、历史基线重建、差异分析、清理影响评估、代码测试与脱敏成果提交。
- 暂缓 T1/T2/T3 新清理批次、回收站清空、应用卸载、永久删除及基于日期的批量清理。既有的高风险自动 purge/housekeep 删除调度应设置 HOLD 或改成只读模式；不应销毁现存扫描快照、历史 Journal 与证据。仅允许为保存新结果必要的程序自身写入；不修改用户内容。
- 如确有系统自身状态轮换需要，优先压缩保留和上限控制，不执行删除历史证据。此前已执行的清理无需倒推或虚报；继续保留与核验原始记录。
- 此阶段成果不是清理 GB 数，而是：能解释全机占用、区分可证实的新增与推断的新增、给出可执行且明确影响的清理清单。实际删除须待用户基于该清单再次批准。

## 1. 全卷同一时点空间基线与对账（P0）

- 采集 C/D total_bytes, used_bytes, free_bytes, timestamp, filesystem, volume_id；再按目录、软件、Windows 系统组件、用户数据、Agent、模型、容器/虚拟环境、日志、缓存、同步目录分组。输出 top 30 目录和 top 30 安装软件/数据产出者。
- 把 Explorer 和 Win32 API 卷级 used/free 与递归文件枚举值分开，不把二者简单视为必须相等。枚举被拒绝目录、系统保留、pagefile、hiberfil、VSS、NTFS 元数据、硬链接、稀疏/压缩文件、AppX、WinSxS 的归因与排除计数；报告无法解释的 residual bytes，而不是自动归类为垃圾。
- 对跨目录硬链接只记一次占用；符号链接/junction/reparse 不追踪循环、云端占位不估算成本地可释放 GiB。报告 logical_size 和 allocated/on_disk_size 的口径。
- 当前邮件锚点（需新扫描重测）：2026-10-19 C used 152.50GiB、scan 91.73GiB；D used 116.57GiB、scan 115.81GiB。重点解释 C 约60.77GiB 差额，不能把它估算成可删空间。
- 报告 scan_start/scan_end、各卷覆盖完整性、访问失败目录与错误数、进程同时写盘的扰动；扫描出错/覆盖不足直接标 PARTIAL，不得宣布全盘审计 PASS。

## 2. 电脑最初状态与最干净状态：明确区分事实和估计

需要形成以下三个互不混淆的基线：

A. EARLIEST_PROVEN：可从本机真正恢复的最早可信证据：历史 inventory、snapshots、系统安装/升级记录、卷影副本或受保存期限限制的 USN/日志、软件安装记录。每条说明对应时间点、来源与覆盖范围。如果没有出厂/首次安装快照，必须明确写 UNRECOVERABLE；不能把安装日期或文件 ctime 推断成精确的出厂目录。
B. CLEAN_OS_ESTIMATE：假设只保留 Windows、驱动、必要运行组件的干净系统占用区间，注明 Windows 当前版本、更新状态、保留存储、pagefile、休眠和测量法；这只是模型估计，不是历史事实。
C. LEAN_WORKSTATION_ESTIMATE：保留用户真正使用的软件与数据之后，一台干净工作电脑合理的容量区间；按软件与数据保留清单拆分 C/D，不把所有非OS文件都当成垃圾。

对比 CURRENT / EARLIEST_PROVEN / CLEAN_OS_ESTIMATE / LEAN_WORKSTATION_ESTIMATE；给出 delta_used、可信度、估计误差与可清理子集。历史事实不足时宁可明确 UNKNOWN。

## 3. 新增文件与时间维度分析（P0）

- 每个文件保存卷 ID、标准化路径标识、文件身份（可得时 NTFS file_id）、bytes logical/allocated、creation_time、mtime、LastAccessTime（只作辅助，通常不可信）、类型、目录、文件归属的应用/进程证据、扫描快照ID。
- 在确有两个有覆盖关系的快照时生成差异：ADDED、REMOVED、MODIFIED、RENAMED_OR_MOVED、DUPLICATE、UNCHANGED、UNKNOWN；通过身份/大小/哈希按预算辅助区分移动、复制与新生，严禁仅靠路径不同就断言新文件。
- 没有旧快照时，按 creation_time/mtime 只能推测新增时间（复制、解压和重装会改变时间戳）。把 VERIFIED_ADDED 与 INFERRED_RECENT 分开显示。
- 统计近7/30/90/180/365天以及用户后来指定日期的各应用、目录、扩展名占用增长。注明所用时间字段，不默认使用 LastAccessTime 决定删除。
- 单独列出“新装软件 + 安装包 + 运行时/模型 + 日志/缓存增长”的应用级空间账本，不能把同一模型文件或包依赖在多个分组重复计数。

## 4. 可清理分析（暂不删除）

- 根据当前扫描建立15-30个按预期净释放GiB排名的 bucket；包括旧Agent/AI日志、模型缓存、浏览器缓存、npm/pip/conda/node_modules/venv、下载包、安装残留、回收站、软件旧版本、同步副本及Windows可由系统工具清理的组件。
- 每桶字段：bucket_id、C/D、当前逻辑/实际占用、文件数、创建/修改日期分布、应用所有者、仍在使用证据、是否可能重新生成、删除方式（文件/官方卸载/Windows支持命令）、预计可释放GiB及不确定性、功能影响、可恢复性、风险等级、需要用户一次批准的范围。
- 给出清理后 C 和 D 的预计状态，但目标 C 已用减1/3、D 已用减1/4只是供方案比较的量化挑战，不能推动提前删东西；若不足要解释缺口。

## 5. 系统需要的最小代码整改

- 复用现有 inventory/evidence/policy 和 disk_scan；集中数据事实源，不新增第二套全盘扫描服务。
- 保存带 schema_version + scan_id + as_of + volume_coverage + manifest_hash 的快照；新旧扫描要同口径对齐，支持跨重启和重跑去重。
- 增加 volume_reconciliation、historical_diff、file_age_cohort、app_space_attribution、cleanup_impact、report 这几类可重用只读产物；与 gov 现有模块组合，避免功能重复。
- 修复近期发现的 release_exec.py 无效时间戳被当作已过30天的问题，以及缺 journal 原始 hash 时误放行；冻结自动删除直到代码负面测试通过且用户重新批准。
- housekeep 改只读与互斥，保留历史快照以支持时间差异；不得再次发生未归因的并发历史数据清理。定时任务此阶段只跑 scan + compare + report。

## 6. GitHub 发布与验收

既有公开仓库 zdmor/public-lab-02，路径 file-organizer/review/2026-10-19/；不创建新仓库。

应提交脱敏摘要：baseline.md、volume-reconciliation.md、earliest-history-evidence.md、file-delta-summary.md、age-cohorts.md、app-space-top.md、cleanup-candidates.md、read-only-e2e.md、sanitized-code/。
不要提交原始用户文件名、完整路径、邮箱、主机私密配置、密钥、同步文件明细、聊天数据、客户/公司资料或原始全盘日志。提交前进行密钥扫描与脱敏人工检查。
如果 N1 GitHub App 仍然是403，只发简短邮件说明 403 并随附可安全公开的小份分段文本给 ChatGPT 代提交；扫描任务不应因网络权限停滞。

验收必须包含：(1) 最新C/D可用、占用与扫描覆盖；(2) 所有主要空间桶的归属与无法解释差额；(3) 至少两份真实时间点快照的同口径 E2E 对比，历史不足须明示；(4) 最早真实记录与两个精简估算情景；(5) 15-30个清理桶及逐项影响；(6) 全流程只有只读扫描、无未经批准的真实清理；(7) GitHub可回读证据与真实commit。

唯一 NEXT：先交付 C/D 占用对账 + 最早可靠状态追溯 + 新增文件分类报告，让用户理解电脑为何越来越满，然后另开批量清理裁决。