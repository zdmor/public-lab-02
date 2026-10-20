# BookKnowledge 只读索引与全量记录校验摘要

- 生成时间：2026-10-20T06:05:18.177Z
- 输入：`A_已完成_可调用技能/index.csv`、`A_已完成_可调用技能/*/拆解记录/**/*Phase*.md`、`F_辅助产出/T注册表.md`、`F_辅助产出/跨书信号索引.md`
- 输出：`BOOKKNOWLEDGE_INDEX.json`（机器可读，相对路径，无绝对路径）
- 性质：**Inventory 级只读审计**；`read_verified_books = 0`，不代表任何一本书已通过内容验收。

## 1. 口径修正

| 指标 | 本轮实测 | 说明 |
|---|---:|---|
| index.csv 行数 | 132 | 索引条目，含非书 |
| 目录数 | 133 | 含 `_bk_scratch` |
| 书目目录 | 128 | 排除 5 个非书目录 |
| 非书目录 | 5 | 3 个 `_孟洪涛` + `SKILL文件` + `_bk_scratch` |
| 临时唯一作品簇 | 123 | 标题归一化候选，不是最终作品数 |
| 重复候选簇 | 5 | 含 1 个版本候选 |
| Phase 类文件 | 804 | canonical 762 + variant 36 + legacy 6 |
| 六槽位齐全的书 | 126 | 不是上一轮的 115（按“正好 6 个文件”算会误伤多文件书） |
| 六槽位不齐的书 | 2 | How to Read a Book (Adler)；The Art of Strategy (Dixit) |
| 有 quality_level 的文件 | 85 | 精读 73 / 框架 12 |
| 有 quality_level 的书 | 22 | 精读 18 / 框架 4；**上一轮把文件数说成了书目数** |
| 未标质量级的书 | 106 | 需补 `phase_status` |
| 有源文件的书 | 117 | 缺 11 |
| 有文本可用（全书文本或源文件） | 122 | 缺 6，这些判 `source_blocked` |
| 有源文件但未转文本 | 1 | 判 `needs_conversion`，补齐 `全书文字.md` 后再拆 |
| 有标准 `全书文字.md` | 72 | 缺 56；非标准命名的 `*_extract.txt` 未计入此列 |
| 有目录骨架的书 | 53 | 缺 75 |
| 已验收书 | 0 | 本索引不做内容验收 |

## 2. Phase 状态字段

| 格式 | 文件数 |
|---|---:|
| `<!-- phase_status: ... -->` | 18 |
| 旧式 YAML 代码块 | 22 |
| 松散 `quality_level:` 行 | 45 |
| 存在标记但解析失败 | 0 |
| 完全缺失 | 719 |

## 3. T 注册表

| 指标 | 数值 |
|---|---:|
| 行数 | 271 |
| 唯一 T ID | 183 |
| 额外出现次数 | 88 |
| 碰撞编号数 | 72 |
| 其中语义不同（ambiguous） | 68 |
| 其中完全相同（exact duplicate） | 4 |
| 8 列行 / 12 列行 | 3 / 268 |
| 字段映射不确定行 | 268 |
| T ID 解析失败 | 0 |
| 每行 T 记录 / 碰撞行 / 未审阅行 | 271 / 160 / 111 |

重复次数分布：1 次 × 111 个编号；2 次 × 56 个编号；3 次 × 16 个编号。

Phase 文件引用到的 T ID：286 个唯一值；不在注册表中的 111 个；注册表有但 Phase 未引用的 8 个。后者可能是索引示例或未落地内容，需人工判断。
另有 7 个非标准片段（如 T-ALG、T-006），不计入 T ID 交叉核对，已单独列入 `t_id_crosscheck.nonstandard_tokens`。
依赖字段：注册表 `depends_on` 0 行、`conflicts_with` 0 行；Phase 文件 `depends_on` 0 个、`conflicts_with` 0 个。字段缺失，悬空依赖检查无法执行。
跨书信号索引：47 个唯一 T ID；47 个不在 T 注册表；仅 8 个在 Phase 文件出现，39 个未出现。该索引是独立命名空间，不能当作注册表或已验证知识。

## 4. 记录级审计状态

| audit_status | 记录数 |
|---|---:|
| `quality_unmarked` | 98 |
| `inventory_ok` | 15 |
| `source_blocked` | 6 |
| `nonbook` | 5 |
| `inventory_with_extras` | 5 |
| `phase_incomplete` | 2 |
| `needs_conversion` | 1 |
| `needs_review` | 1 |

## 5. 回读样本（2026-10-20）

抽样协议：1 随机（seed=`2026-10-20`）+ 1 关键 + 1 争议；另加 1 个跨书索引悬空编号。四样本均 `read_verified=false`，不构成整书验收。

| T ID | 类型 | 注册表行 | Phase 命中 | 原文核验（首个命中行） | 结论 |
|---|---|---:|---:|---|---|
| `T-SHM-010` | random | 0 | 1 | up_bar_definition:L1634|L3191；down_bar_definition:L1839；weakness_on_up_bar:L1630|L2355；strength_on_down_bar:L1836 | `source_supported_but_unregistered` |
| `T-LIV-003` | key | 2 | 7 | industry_group_concept:L40|L611|L612；sister_stock_concept:L42|L617|L711；top20_parameter:L928|L1706；chapter5_body_heading:L395；chapter8_body_heading:L576 | `concept_supported_locator_and_parameter_unsupported` |
| `T-VPA-001` | controversial | 2 | 6 | breakout_with_volume:L446；rising_prices_rising_volume:L514；chapter4_heading:L414；chapter7_heading:L1003；chapter8_heading:L1126 | `concept_supported_parameter_derived` |
| `T-AMH-01-01` | cross_index_discovery | 0 | 0 | adaptive_markets_hypothesis_zh:L1046|L1312|L1319；maladaptation_zh:L1398|L1402|L1420 | `phantom_cross_index_only` |

跨书索引悬空引用：`T-AMH-01-01` 在索引出现 5 次，注册表 0 行，Phase 0 次；Adaptive Markets 全部 Phase 文件共 0 个 T ID。

重复候选 5 簇，全部 `keep_both_pending_review`，本轮不合并。
`source_blocked` 6 条：3 条有含文本的重复候选兄弟（不是独立作品缺口），3 条为无文本独立作品——淘县大师传之炒股养家；Encyclopedia_of_Chart_Patterns；Technical_Analysis_of_Stock_Trends。
`phase_incomplete` 2 条：How to Read a Book (Adler)（slots 0,1,2，完整兄弟=false）；The Art of Strategy (Dixit)（slots 0,1,2，完整兄弟=true）。

## 6. 已知问题

| 代码 | 严重度 | 证据 | 状态 |
|---|---|---|---|
| `COUNT_BASIS_MISMATCH` | high | index.csv=132; dirs=133; books=128; unique_works_provisional=123 | open |
| `QUALITY_FILE_VS_BOOK` | high | quality_marker_files=85 (精读 73 / 框架 12); quality_marker_books=22 (精读 18 / 框架 4) | open |
| `PHASE_STATUS_MISSING` | high | phase_like_files=804; quality_marker_files=85; comment=18; yaml=22; loose=45; missing=719 | open |
| `T_REGISTRY_SCHEMA_MIXED` | high | rows=271; 8-col=3; 12-col=268; ambiguous_field_mapping=268 | open |
| `T_ID_COLLISIONS` | high | rows=271; unique=183; extra=88; colliding=72; ambiguous=68; exact_duplicate=4; distribution={"1":111,"2":56,"3":16} | open |
| `DUPLICATE_WORKS` | medium | clusters=5; extra_records=5; edition_clusters=1 | open |
| `PATH_REFERENCE_STALE` | medium | SKILL.md 引用 A_已完成_可调用技能/辅助产出/T注册表.md 与 .../跨书知识索引.md；实际为 F_辅助产出/T注册表.md 与 F_辅助产出/跨书信号索引.md | open |
| `CROSS_INDEX_REGISTRY_DISCONNECT` | high | cross_index_ids=47; in_registry=0; not_in_registry=47; in_phases=8; not_in_phases=39 | open |
| `DEPENDENCY_FIELDS_ABSENT` | high | registry depends_on=0; conflicts_with=0; phase depends_on=0; conflicts_with=0 | open |
| `READ_NOT_VERIFIED` | high | read_verified_books=0/128 | open |

## 7. 复现

```bash
cd BookKnowledge
BOOKKNOWLEDGE_DIR="$(pwd)" node F_辅助产出/audit/2026-10-20/build_bookknowledge_index.js
```

脚本只读 `index.csv`、`拆解记录/**/*Phase*.md`、`T注册表.md`、`跨书信号索引.md`，不修改任何书目、Phase、T 编号或 WTOS 生产代码。

