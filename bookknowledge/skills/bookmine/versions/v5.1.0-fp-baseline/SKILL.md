---
name: bookmine
description: 从书籍与文档中提取真实、可溯源、可检验、可复用的知识资产；将作者主张、研究者推导、独立证据、生产采纳严格分层。支持通用书籍和交易/工程领域扩展。仅在明确要求研究或拆书时调用。
version: 5.1.0-fp-baseline
status: FROZEN_SPEC_NOT_VALIDATED
baseline_date: 2026-10-23
origin: N1 BookMine v5.0 (2026-07-22)
scope: methodology-specification-only
quality_dimensions: [source_integrity, extraction_fidelity, claim_traceability, reasoning_quality, empirical_validity, downstream_usability]
book_types: [A_method, B_narrative, C_theory, D_reference, E_hybrid]
original_owner: N1 BookKnowledge
discussion: https://github.com/zdmor/public-lab-02/issues/3
---

# BookMine v5.1.0-FP | 第一性原理研究基线

## 0. 根本问题与不变量

**根本问题**：书是作者留下的论证载体，不等于事实，也不等于有效方法。如何把有限上下文中的书转换成未来可调用、可校正、可验证的知识，同时不伪造、不混淆、不重复读、不直接误导生产决策？

由此推导六条不变量：

1. **先识别研究对象**：作品 work、版本 edition、物理文件 source、提取文本 extract、断言 claim、策略/操作假设 hypothesis、验证 evidence、系统采纳 adoption 是不同对象。数量和状态不得混用。
2. **原文优先于二次解释**：作者说了什么必须能回指对应版本的原始位置。原始书籍不准确的观点也允许忠实记录；未经原书支持的数值不得以作者名义出现。
3. **事实与价值分离**：抽取准确 ≠ 作者正确 ≠ 在 A 股有效 ≠ AXI 允许正式使用。评估可以记录分歧，但不能阻断忠实提取；伪引文、错书、无效定位、错误归属必须阻断“溯源通过”。
4. **不确定性是正式数据**：未知、未读、未量化、未验证、样本不足、文本缺图、版本冲突均须有原因；不许用 DONE/PASS 遮蔽。
5. **最小必要结构**：只有能提高可验证性、检索效率或实际决策质量的字段/阶段才保留；不为了填模板制造结论。不强迫历史、哲学、技术手册产出交易信号。
6. **通过证据而非标签升级**：验证必须可复现、可回读，且结果能相对于基线证明增加价值。GitHub commit 锁定“规范文本”而不是自动证明方法论有效。

## 1. 边界与工作模式

- 默认运行 **EXTRACT_ONLY（提取与出处检查）**。用户要求评审时为 CRITIQUE；要求验证时为 RESEARCH_EVAL；要求接入系统时为 ADOPTION_PROPOSAL，仍需项目 Owner 的独立验收。
- 模式间没有自动跃迁。每一阶段提供独立状态、证据和下一步，不以“完成 Phase 0–5”替代验收。
- 保留旧版 Type A–E、Phase 0–5、框架/精读两档、T 编号、交易领域扩展；**重定义通过条件**，避免只要文件齐就宣称已读。
- 兼容 N1 本地 BookKnowledge；本基线只定义规范，不执行文件迁移、重编号、书籍删除、自动交易、GitHub Owner 变更。
- 本规范是公开的原创方法与结构，**不得包含原书全文、大段替代性转述、完整表格/图片或私有凭证**。

## 2. 输入检查：建立唯一对象链

每次拆解记录下列对象，禁止将其压缩成“书名”一列。

| 对象 | 最低字段 | 检验 |
|---|---|---|
| Work | work_id、标准书名、作者、语言、主题 | 区分非书资料；重复作品只标候选 |
| Edition | edition_id、版次、出版时间、ISBN（若可得） | 不同版本可能有不同页码、内容与参数 |
| Source | source_id、相对路径、文件类型、SHA256、提取能力 | 源文件保留于授权私有位置；公开只披露脱敏元数据 |
| Extract | extraction_id、source_id、提取器/版本、文本 SHA256、章节映射、图表遗漏数 | 检查乱码、漏页、公式、图片、图表、章节错配 |
| Claim | record_id、canonical_t_id（若已注册）、claim_kind、source_locator、原意概括 | 一个断言对应明确出处和归属 |
| Hypothesis | hypothesis_id、由哪些 claim 推导、操作化变量、研究假设 | 不能把拆解者建议伪装成书中原话 |
| Evidence | evidence_id、数据时点、检验协议、结果、局限、SHA | 无检验则明确 NOT_TESTED |
| Adoption | 项目 Owner 引用、模块、批准版本、正式状态 | 未经 Owner 审批永不标记 ADOPTED |

路径一律先探测再使用。N1 2026-10-20 审计的实际注册表路径是 F_辅助产出/T注册表.md，跨书索引为 F_辅助产出/跨书信号索引.md；旧 v5.0 frontmatter 指向 A/辅助产出，已过期。实现应配置 BOOKKNOWLEDGE_DIR 并将相对路径校验失败显式报出，不自动创建替代注册表。

## 3. 来源质量门：先证实“读到的是什么”

- 明确 TEXT_OK / PARTIAL_EXTRACT / SCANNED_UNREADABLE / EDITION_UNCERTAIN / SOURCE_MISSING。
- PDF、EPUB、DOCX、图片扫描分别走适用的解析器。技术书必须检查表格、公式、图片及图注的损失；扫描件在确认可读取前不可声明已读。
- 抽取文本必须带章节锚点及可复算的行/段标识；章节标题、图片标记、版本信息和源 SHA 应保留。
- 先做质量检测再批量处理。失败只能补提取或降级，不得靠模型猜缺失原文。
- 同名不同版本与不同名同一作品仅输出 duplicate_candidate / alias_candidate，禁止自动删文件或合并两个不同事实源。

## 4. 按内容角色选择提取方式

沿用 BookMine 五类：A 方法论体系、B 叙事案例、C 概念框架、D 参考工具书、E 混合型。书有主类型，**条目按真实论证角色**使用对应格式：

- **方法/操作**：问题场景 → 前提条件 → 执行步骤 → 退出/禁止条件 → 可观察结果；未提阈值写 SOURCE_NOT_QUANTIFIED。
- **理论/机制**：定义 → 因果或形式化论证 → 必要假设 → 可反驳预言 → 边界。
- **认知/决策**：默认错误直觉 → 新模型 → 使用触发场景 → 常见误用。
- **叙事/案例**：已知事实 → 当时可用信息 → 决策 → 结果 → 对照解释 → 可迁移性限制。不得从故事虚构统计胜率。
- **工具/参考**：术语/公式 → 单位和变量 → 生效条件 → 文献定位 → 使用例 → 失效条件。不能省略公式表格的提取完整性检查。

对复杂书籍增量读取，按真实章节边界划块；每块记录 read_block、范围、源 SHA、抽取状态和冲突。未覆盖的章节必须列出，不得把存在全书文本等同于模型完整阅读。

## 5. 六阶段最小闭环（按类型裁剪产物，不裁剪真实性）

### Phase 0 — 对象登记和问题定义
定义“这本书用来回答什么问题”、适用项目、Work/Edition/Source、书类型、读取计划和预期知识用途。可判断只需索引、需要精读或不适用；**任何价值评估都不阻断忠实提取**。

### Phase 1 — 作者论证地图
列作者核心问题、假设、主张、关键证据、反对观点及章节分布。对每个主张区分“书中主张”“独立世界事实”“读者推论”。

### Phase 2 — 原子化知识卡
原则是一个卡片只表达一个可独立核查的断言。默认推荐 3–7 个一级框架；有价值的次级条目进入索引，不为控制数量而删证据。每张卡至少包括：

- record_id / work_id / edition_id / source_sha256；
- canonical_t_id（只有在注册表无冲突且实际分配成功时填写）或 T_ID_UNRESOLVED；
- claim_text（自己概括，不复制大段原文）；
- claim_kind = AUTHOR_EXPLICIT / AUTHOR_SYNTHESIZED / RESEARCHER_DERIVED / EXTERNAL_EVIDENCE；
- source_locator = 章节 + 段落/行号/页码 + 文本版本；
- source_support = SUPPORTED / PARTIAL / UNLOCATABLE / CONTRADICTED；
- assumptions、counterexample、applicability、falsifier、depends_on、conflicts_with；
- source_fidelity_status、research_evidence_status **各自独立**。

拆解者公式和参数单列 research_parameter；必须标 ORIGINAL / DERIVED / OPTIMIZED，且无量化不得填默认倍数、胜率和回撤。

### Phase 3 — 最强解释与严格反驳
对核心主张依次进行：①善意强化（steelman）②必要前提 ③支持证据 ④有力反例/替代解释 ⑤成本与副作用 ⑥失效边界与反驳条件 ⑦当前结论与待补证据。结论可为 SUPPORTED_AS_AUTHOR_CLAIM / CONTESTED / UNRESOLVED，不用“观点不可靠”删除作者主张。若作者的因果结论无法区别样本选择、反向因果和巧合，明确保留为假设而非事实。

### Phase 4 — 应用映射，不直接落地
把已追溯知识映射到具体问题和项目现行事实：现有规则、差异、可检验的增量假设、预计成本、数据可得性和风险。仅提出候选和验证需求；**不能因读书改变生产代码或策略权重**。
- AXI：候选映射 M0 数据/PIT，M1 市场风险，M2 行业主线，M3 技术候选，FINALIZER 风险/交易许可；任何映射只是 RESEARCH_CANDIDATE。
- 工程：标准版本、工况、材料、计量单位、试验条件和可重复实验必须单独核验。
- 通用管理与哲学：不强行创建财务收益率；按可观察决策质量与反例检验。

### Phase 5 — 独立检验和回写
为可验证主张定义事先写定的基线、数据、测试窗口、拒绝标准和失败方式。交易领域至少检查 PIT、退市/幸存者偏差、复权、交易限制、成本、样本外、滚动前向、参数稳定性、市场状态、风险与收益；指标可包含 Calmar，但不能仅看 Calmar。没有完整独立检验时输出 NOT_TESTED 或 INSUFFICIENT_EVIDENCE。
验证失败不修改或抹除原始 claim；添加新 evidence 与记录状态，保持历史追溯。任何 AXI 正式采纳须经过对应 GitHub Owner 的独立审查和模块验收。

## 6. 质量状态必须分层

每本书最少有以下**独立维度**，状态不准相互推导：

1. inventory_state：DISCOVERED / INDEXED / DUPLICATE_CANDIDATE。
2. source_state：MISSING / PARTIAL / VERIFIED_EXTRACT。
3. reading_state：NOT_READ / FRAMEWORK_ONLY / CHAPTERS_PARTIAL / FULL_READ_EVIDENCED。
4. traceability_state：NOT_CHECKED / TRACE_PARTIAL / TRACE_VERIFIED / TRACE_FAILED。
5. extraction_state：DRAFT / REVIEWED / REJECTED_FOR_FIDELITY。
6. hypothesis_state：NOT_APPLICABLE / PROPOSED / TESTED_REJECTED / TESTED_INCONCLUSIVE / TESTED_SUPPORTED。
7. adoption_state：NOT_PROPOSED / PROPOSED / OWNER_APPROVED / PRODUCTION_VERIFIED。

**强制不变量**：Phase 文件齐全 ≠ FULL_READ_EVIDENCED；精读标记 ≠ TRACE_VERIFIED；来源准确 ≠ TESTED_SUPPORTED；卡片 PASS ≠ OWNER_APPROVED。抽样通过只能称 SAMPLE_CHECK_PASSED，绝不称所有条目通过。

只有当各维度所需证据独立满足时，才能使用相应状态；不设单一总体 PASS。

## 7. T 编号、索引和升级约束

- 既有 T ID 是历史引用，不能按字符串去重，也不能自动大规模重编号。
- legacy_t_id、canonical_t_id、record_id、work_id、edition_id 必须区分；同一个旧 ID 对应多个不同知识点时标 AMBIGUOUS，并阻断自动 canonical 绑定。
- 以当前注册表复核结果为准；未解决冲突的新条目可以使用独立 record_id 保存，但不冒充已正式分配 T ID。
- depends_on / conflicts_with 要能解析到明确 record_id；没有填写不代表“没有冲突”，须声明 NOT_EVALUATED。
- 统一索引由一个事实源生成，README、跨书索引、质量统计从同一数据导出；不得有第二套手工权威计数。
- 路径修正、别名映射、历史冲突修复均需要保留变更日志和可逆映射；不触碰原有用户文件直到授权。

## 8. Agent Skill 输出结构（按需加载）

先输出极薄的 SKILL.md：何时使用、不能做什么、决策流程、状态语义、主题目录。大型书细节按需加载 references/chapters/、glossary、patterns、cheatsheet 和 evidence index；不得把整本书在每次调用时放入上下文。
生成可调用技能还必须包含：来源标识、触发条件、至少一组正例和反例、禁用条件、可验证的输出约束、无源/含糊时的正确退化响应。
遵守版权：书籍原文保留在授权私有存储，GitHub 公共讨论只可提交原创方法文档、脱敏元数据和极短的必要引文。

## 9. 测试驱动的 Skill 研究

本规范是**冻结研究基线**，尚不是验证通过的 Skill。升级为可正式启用版本前必须执行同题、同源、同提示的对照评估：
- A（交易方法）/ C（理论框架）/ D（技术参考）各取一本已具备合法原文的书；
- 旧 BookMine v5.0、v5.1-FP 与外部 book-to-skill 作为不同流程比较；
- 对来源回指率、伪造阈值率、作者/研究者归属准确率、T 引用一致率、缺源正确拒绝率、实际任务回答质量、计算成本评分；
- 使用盲评和故障注入，分开记录“知识抽取保真”与“研究结论有效”；接受标准见同目录 ACCEPTANCE.md。
不可把预期改善写为实际结果，未运行标 NOT_RUN。

## 10. 旧版兼容与锁定声明

- 继承：v5.0 的 Type A–E、Phase 0–5、双质量档、渐进式提取、交易深度叠加、T 注册、评估留存。
- 改变：由标签式 PASS 改为分层证据状态；由书名驱动改为 Work/Edition/Source/Claim；由“按模板完成”改为可回读的原文定位；研究假设与作者阈值强制分离；模型方法的相对效果需做盲测。
- 不改变：N1 当前 BookMine.skill 文件、旧 Phase/T 历史、AXI 正式规则、GitHub 项目 Owner。
- 本版本号 v5.1.0-fp-baseline 仅表示**规范文本冻结**，不表示精读质量、代理调用效果、AXI 收益或生产验收 PASS。正式启用需要独立评估后另行批准。
