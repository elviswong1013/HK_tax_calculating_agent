# 002 — 税负正确性修复（第一阶段）— 软件设计文档（SDD）

Status: Implemented
Created: 2026-10-14
Updated: 2026-10-20（P3 final5 oracle PASS + acceptor ACCEPTED；stage1 实施关闭；状态 Approved→Implemented；历史 OPEN 边界保留：45-red/W1b 原始写前字节/F3 全量/pre-seal/21 项 history_unavailable/视觉 B/D/F/stage2/3 未扩；代码/测试/文档语义未变）；2026-10-19（P3-W2 文档同步：脚本 002 追溯命名空间与离线固定束报告接口已实现；根全量/真实 Chromium/视觉证据见 W2 EV；规范语义未改，状态仍 Approved / NOT Implemented）；2026-10-14（Approved 第五稿：按 council NEEDS REVISION 四项合同＋`bounded-closure-design.md` 完成机械修订；formalreview4 APPROVED 为第四稿历史证据；修订对照见 `.slim/deepwork/tax-correctness-stage1/sdd-revision-r5.md`）；用户 Gate 2 于 2026-10-14 明确批准「批准第五稿，进入实施」；状态 Approved（仅 stage1；NOT Implemented；批准事件≠设计/测试/验收证据）
PRD: docs/prd/002-tax-correctness-remediation.md（Approved，Gate 1 2026-10-14）
模板: specs/_template.md
索引: specs/README.md
证据: docs/research/002-tax-correctness-evidence.md（Evidence-only）
完整事实契约附件: docs/design/005-tax-correctness-fact-contract.md（Implemented（stage1），Annex 005；封闭字段表／谓词表／ledger／候选真值表／状态表／profile/digest／槽位退役／trace／失效协议／observer 所有权／availability 调用闭包／条件 assessment／S7 历史配偶）
验证附件: docs/design/006-tax-correctness-verification.md（Implemented（stage1）；完整历史断言处置与验证计划）
只读基线: specs/001-hk-tax-agent.md（Implemented，不修改）；docs/design/002-hk-tax-technical-contract.md（Annex C，Approved，技术契约权威）；docs/design/003-hk-tax-legal-contract.md（**Annex D Status: Approved**（随 001 用户 Gate2 2026-10-08 批准）；本 CR 不撤销其效力，仅列 002 增量）；docs/design/004-tax-correctness-ui.md（Implemented（stage1），UI delta，视觉意图不变）

> 状态声明：Approved 第五稿；formalreview4 APPROVED 与原 council NEEDS REVISION 均保留为历史证据；本稿按 `bounded-closure-design.md` 完成修订，**受影响正式审查 APPROVED（`review-plan-r5.md`）＋有界圆桌 READY FOR LAST P0（`council-closure-r5.md`）＋最后 P0 attempt3/3 GATE READY（`gate-p0-r3.md`）已归并为设计门禁；用户于 2026-10-14 明确批准 Gate 2（原话「批准第五稿，进入实施」），本状态自 2026-10-14 为 Approved**；**2026-10-20 由 Approved 更新为 Implemented（P3 final5 双终验：oracle PASS＋acceptor ACCEPTED）**。获批范围＝**stage1 P1→P2→P3 严格 TDD**（阶段 2 扣除、阶段 3 完整 PA 另行审批）；**历史 OPEN 边界保留；批准/验收不构成完整历史证明**；仍不撤销 001 既有审批（PRD §6.1）；本文不创造测试、不承诺实现或运行验收通过。

## 0. 摘要与阅读指引

### 0.1 与 PRD 的关系

- AC-1..AC-13 与 REQ-1..REQ-13 **一一对应**（§8）；PRD 范围／非目标直接引用 PRD §2，本文只细化设计。
- 细化 PRD §2.1 三项：Part 5 免税额资格、夫妻薪俸税输入与确认、暂缴税年度边界；并同步受影响 AI／记录／导出／回放／更新／覆盖与版本。
- 全部封闭字段、时间谓词、ledger、候选真值表、组件状态、存储判定、profile/矩阵/槽位、trace 与失效协议放 Annex 005（§2–§17）；完整历史断言处置与验证计划放 006；本文正文保留决策与引用，避免规格/附件漂移。

### 0.2 标记约定

- `【待核验】`、`【拟名】`、`【外部依赖】`、`【OPEN】` 同前；`【001 已批】`＝Approved／Implemented 契约，本 CR 不改；`【G2 决定项】`＝曾须用户 Gate 2 明确批准的增量（2026-10-14 已批准，仅 stage1；2026-10-20 已实现（stage1））。

### 0.3 验证与审批归属

编排者整合 → @plan-reviewer 复审 → 高风险跨系统并行 councillor×3 → @council 汇总 → @oracle 设计风险闸 → **用户 Gate 2** → TDD（test-writer Red／executor Green）→ 分阶段 oracle 闸（P1 后／P2 后）→ P3 完整套件＋真实浏览器 → 只读 @acceptor ACCEPTED。任一闸未过不得进入下一阶段；协调者负责法律与跨调用方对账。

## 1. 范围与边界

### 1.1 覆盖范围（阶段 1）

薪俸税与 PA 的普通家庭 Part 5 免税额（子女含出生／次年额外额、父母／祖父母、兄弟姊妹、单亲、本人伤残、伤残受养人）资格与金额；夫妻薪俸税事实的 API／网页录入、校验、确认；既有有限 PA／no-PA 方案（`pa_self`、`pa_joint` vs `no_pa`）的免税额处理；下一年度规则缺失时的暂缴边界；受影响组件（AI 工具、记录步骤、打印／导出、历史查看／回放、覆盖矩阵、版本说明）同步。

### 1.2 明确排除

引用 PRD §2.2：阶段 2 新增扣除（除长者住宿照顾开支与父母／祖父母互斥关联）、阶段 3 完整夫妻 PA 方案枚举／亏损／跨税项缴款退款比较、物业／印花算法改动、复杂收入来源与争议资格程序、自动报税／付款／税单、依赖安装、服务重启、自动提交推送。**不做**通用规则 DSL、七税统一引擎、任意 JSON 导入、admin HTTP 升级入口或通用迁移框架。**不新增扣除类型**（ERCE 仅既有 cap 计算、关联 subject 与互斥对账）；**不新增隐私/信任边界能力**。

### 1.3 支持矩阵字段（AC-1）

覆盖数据源每项 `support_level ∈ {computable, needs_input, manual_handoff}`（由 `unimplemented` 迁移）＋`phase`（1/2/3）＋`limits_zh`：阶段 2／3 项目一律 `manual_handoff`＋“另行需求与规格审批”；PA 标“评税选择（本阶段仅纠正既有 PA/no-PA 方案免税额；页面仍 API-only）”。覆盖矩阵逐普通类别／阶段给出网页／API／聊天一致映射（现 `app/web/coverage_data.py` 仅 5 条 unimplemented，全部更新）。

## 2. 架构决策记录（ADR）

| # | 决策 | 理由 | 影响 | 被拒备选 |
| --- | --- | --- | --- | --- |
| ADR-1 | 纯函数共享解析器 `app/engines/allowances.py::resolve_allowances(context, year, scenario, rules)`＋唯一 Part 5 年度矩阵 `data.allowances`（新 `app/rules/bundles/allowances.py`）；薪俸／PA／no-PA 共用 **subject 注册表＋逐年事实**（Annex 005 §3–§4） | 现状 salary／PA 各自解析资格、各自存额度、各自生成步骤（PRD §6.1） | 两引擎删除各自 child/parent 判定；决定金额的明细进 trace；两税 input 升 2.0.0 | 各自复制修补；通用 SQL 式规则 DSL |
| ADR-2 | 每场景独立 claim ledger；键 `(kind, claimant, subject)`＋类互斥矩阵（Annex 005 §8）；PA 与 no-PA ledger 不互相消费 | 互斥只应作用于同一评税方案内部；child＋disabled、parent＋额外额不得误杀 | 重复／跨人／跨类别冲突按 scenario 执行；s.31A entitlement 不要求基本 claim | 全局单 ledger |
| ADR-3 | 版本：engine `0.2.0`（全局）；薪俸／PA input `2.0.0`，其余 5 税维持 `1.0.0`；bundle `rules_schema_version 2.0.0`；`/api/v1` URL 不变；**七税 prepare 共同升级 `If-Match` 协议＋新增 invalidate 端点＝显式 G2 delta**，运行时 epoch/revision 附加于 meta/version 响应、**不入 binding/core** | 语义与字段均变更；其余五税 input/算法本期不动但公共确认协议须一致 | binding 四元组继续承载；旧 2 税 schema 拒绝新计算；其余五税调用方须适配 If-Match（§4.2） | 全税项齐升 schema；静默兼容；把 epoch/revision 塞进 binding |
| ADR-4 | 失效协议（Annex 005 §12）：新增 `POST /api/v1/calc/invalidate`（CAS 请求 `expected_session_epoch/input_revision`）；全 7 prepare 需 `If-Match`；匹配后**先** revision++ 与失效、**再**校验 body；confirm/execute **原子**核对与消费；已 in-flight 字节不可撤回，保证自服务器线性化点起 | 现代码仅 prepare 更新 revision，旧 confirmation/未消费 consent 可在未重提交时继续有效；peek→无条件 mark 存在竞态 | 编辑/采用→invalidate→ACK→新 prepare；迟到响应丢弃；records 保留；CSRF/错目标不消费语义不变 | 只改 UI 禁用；乐观锁隐藏状态 |
| ADR-5 | 引擎 trace 放现有 `steps`，按 Annex 005 §14 封闭 tagged steps（限 6 kind、有限 op 名、`Exact` 分数串、确定性排序）；`CalculationCore` 十一字段与六金额键不变；web/print/`read_result` **只读保存结果**，不重算、不重建旧记录 | `steps` 属哈希对象，方案明细须可哈希审计；现网页重生成额度说明、records 不保存 scenario | 历史旧记录按原 core/input/steps/binding 字节渲染（**仅 AI `read_result` 对旧无 tag 才 steps=[]**；web/print/download 不重建）；label 缺省降级文本 | 新 envelope 字段；仅 warnings |
| ADR-6 | 取整与宽减**按实际执行分支**冻结（Annex 005 §14）：salary `salary_floor_min_v1`＋**rebate floor**；PA individual/joint `pa_unit_floor_min_v1`＋**rebate ceil**；CompTP **仅**真实 no_pa 薪俸子分支（callee 链按 D6.2a），不泛化到 joint PA；property/business 各自既有 floor 分支 | Annex D 已冻结三年度行为；joint PA 行是**现行产品约定**（非独立官方观察到 joint 分支），trace 只记真实执行链 | 本 CR 不改任何 floor/ceil 点；PA 终额 floor 保留；不得宣称未发生的执行链 | 合并 salary floor 与 PA 分支；“PA 全部 raw 比较” |
| ADR-7 | year-scoped 事实＋**逐条款 ANY/ALL 有限谓词**（Annex 005 §7；来源＝已核法条原文）：`TF` 左闭右开；量词逐谓词固定（ANYTIME／ENTIRE_YEAR／年度合计／指定窗口），仅法定相关条件求交；半开区间与部分年度事实合法 | AC-2／AC-9：异时点事实不得拼资格；统一 intersection 会误拒新生儿/部分年度情形（s.31 `at any time`） | 无通用 time DSL；resolver 只按谓词表求值；单亲排除 `ANY(married AND NOT apart)` | 自由表达式；年度末年龄替代；全事实两两求交 |
| ADR-8 | 发布路径最小化：`HKTAX_DB_PATH` 显式指向新空库加载随发行固定 v2 seed；空库判定＝五态状态表（Annex 005 §15／006）；仅在“真空库”seed；兼容守卫覆盖五入口；不做 admin／迁移框架 | 现 `main.py` 捕获任意 AppError 即 seed、`store.py` 把空库与损坏混同 | 损坏指针不当空库；7 个执行目标旧 replay 全部因引擎不匹配拒绝（原查看／导出不动） | HTTP 升级入口；自动迁移旧 JSON |
| ADR-9 | 金额与资格口径三分离＋**原始资格事实**：`employment_income_nai`（s.12 调整后、Part 4A 前）／`has_assessable_salary_income`（ss.11B–D 存在性）／`has_income_assessable_under_ordinance`（s.41(1A) 全来源）；PA 年龄/居港由 `birth_date`＋`ordinarily_resident_hk`/`both_parents_dead` TF 派生，**删除** `pa_age_condition_met`/`pa_residence_condition_met`/`base_kind` 权威结论 | s.29/s.41(1A) 是法定测试，不得由零 NAI 推断；整体法律结论不得由客户端采信 | 夫妻判定（S1–S7）、PA 候选（P1–P5）按 Annex 005 §9 真值表 | 以 NAI=0 推断无 AI；客户端资格 flag |
| ADR-10 | 组件状态（Annex 005 §10）：PA 本阶段**恒 partial**、`balance` 恒 null＋`payment_comparison` 范围外；**任何 salary partial 一律 balance=null**，仅 complete 时按本年 final−paid；`best_scenario` 仅依所列合法候选本年 final＋资格是否确定（独立于 paid/next；任何可能竞争 unknown 候选 → null）；显式选中已知候选可保留其 final；blocked 六键全 null（C3.3/C3.4） | 现代码“缺 paid 无 best”与候选确定性绑定错误；PA 跨税项比较属阶段 3 | 供 Gate 2 决定的状态增量；旧断言按 §9.5／006 §4 逐节点映射 | PA 冒称 complete；best 与 paid 混绑 |
| ADR-11 | 冻结语义 profile：`frozen_semantics.profiles` 增 `part5_shared_v2`＋`semantic_digest=sha256(canonical_json_bytes(P))`（Annex 005 §15.1）；digest 只覆盖规范化不可变语义，不含金额/文件哈希/排版；**不增第五 binding 字段** | 复审2 第 8 点：digest 混入金额会让 DATA-only 更新变语义变更 | 引擎持固定 P＋预期 digest；兼容检查双向核验；六条旧 salary/PA 路径本期全部 stop-auto（仅观察，无新 auto slot） | digest 含 amounts；binding 加字段 |
| ADR-12 | 撤回未获批准的任意资源上限：取消“日期 1900–2100”“TF ≤32”“每人 32 物业”等新增 cap，**沿用 C2.9 既有上限（1MB／1000 项／子女 ≤9）与既有明确限制**；Money/Ratio 输入词法完全沿用 C2.1/C2.10（尾零接受并规范化，`"2/6"` 约简为 `"1/3"`），不引入“必须已 canonical”的新拒绝 | 复审2 协调者意见与 C2 兼容性；防止以新限制伪装已批契约 | 域约束仅保留每年恰 1 self、最多 1 spouse、注册引用完整性 | 任意日期范围/条目数 cap |

## 3. 数据模型与存储（契约全文见 Annex 005 §2–§5）

### 3.1 唯一事实形状（薪俸税与 PA input v2.0.0 同一信封；封闭）

- 信封 `{tax_type, schema_version:"2.0.0", input}`；`extra="forbid"`；手写封闭字段表（`validation.py` 现行 `_REQUIRED/_ALLOWED`＋AppError 风格）。
- `input` 顶层封闭键：`year_of_assessment`、`subjects[]`、`years{YA:Year}`、`hk_presence_days?`、`assumptions?`；`years` 只允许目标 YA＋紧邻下一年。
- 每年：`participants[]`（恰 1 self、≤1 spouse）、`subject_facts`（对象映射）、`household`、`relations[]`、`financials{pID}`、`care_payments[]`、`claims[]`、`assessment`、`manual_issues[]`、`provisional_paid?`。
- 资格事实**独立于 claims 存在**（同 subject/year）；`claims[]` 只声明意图与引用；`claims=[]`＝显式不申索；已提交 claim 缺触发路径必需事实＝needs_input。
- `Tri`＝原始 `true/false/"unknown"`（唯一例外：既有扣除项 legacy `eligibility` 包装）；缺失≠false≠`"0"`；金额/比例字符串语义按 C2；稳定复用仅 `subject_id`＋已知 `birth_date`；`assessment` 另含 actual/hypothetical 独立 salary-joint 提名字段（§5.3）；`basis/candidates/selection` **无条件必填**，`actual` 对象及其成员按活动消费者条件出现（Annex 005 §4.10；省略不写回 none/ordinary/日期）。
- 有效完整示例（salary／PA）与逐字段表：Annex 005 §4–§5。

### 3.2 逐 claim 路径要点（全表见 Annex 005 §7）

- **child**：`SAMEPOINT(关系有效＋存活＋未婚＋claimant 维护＋年龄路径)`；三路径（<18／18–<25 全日制教育 TF／18+ 伤残不能工作 TF）**替代**；**无一般居港条件（不得保留旧断言）**；出生年/次年额外额独立金额＋独立 cap。
- **parent／grandparent**：高档 `ANY(居港 AND (60+ OR <60 且 DAA 资格))`；55–59 档 `ANY(居港) AND ANY(55–59) AND 未达 60 AND ALL(NOT DAA)`；供养独立：年度现金 ≥12,000 **或**连续 6 个日历月非足值同住；全年同住额外额独立 claim；月末边界/死亡年度/未核亲属＝窄 manual，不整类禁算。
- **parent_cohabitation_additional**：基本 entitlement 成立＋`ALL(cohabit_non_full_consideration, YA)`；反向无要求。
- **single_parent**：child **entitlement**（不要求 claim）＋`ANY(sole_or_predominant_care)`；排除 `ANY(married AND NOT living_apart)`（后来分居不能抵消）；每 claimant/scenario ≤1。
- **personal_disability／disabled_dependant**：`ANY(DAA)`；后者＋对应基本 entitlement（含 spouse 路径：分支 A 或合法 joint salaries/joint PA）；**不要求基本 claim 或扣除项**；s.26D 原始事实可作为受养人定义替代基础（Annex 005 §6.2）。
- **special_situation**：true/unknown → 该 claim manual_handoff＋locator，可提交、可定位，不整类禁算。

### 3.3 规则矩阵、profile 与存储

- `data.allowances.<year>` 唯一矩阵，逐组件 `{amount, cap, status}`；`not_applicable_verified`＝已核不适用（2024/25–2025/26 following-year），键缺失＝未知 → `E_RULES_STATE_UNVERIFIABLE` 拒算；三年度全表＋共享 MPF／宽减单源＋ERCE cap 原源见 Annex 005 §15.2。
- 固定 profile `part5_shared_v2`＋digest 算法＋**六条旧 salary/PA 路径本期全部 stop-auto（仅观察，无替代 auto slot）**＋8 条不受影响路径：Annex 005 §15.1/§15.3。
- 存储不变：SQLite 规则束（单指针原子发布）＋会话内存 records；不新增持久化；五态判定与五入口守卫（Annex 005 §15.4）；新 v2 seed 由发行版固定内容＋provenance 组成。

## 4. 接口契约（API、错误分类法、版本）

### 4.1 版本表

| 项 | 本 CR 前 | 本 CR 后 | 说明 |
| --- | --- | --- | --- |
| engine_version | 0.1.0 | **0.2.0**（全局） | `/api/v1/meta/version`；binding 绑定 |
| rules_schema_version | 1.0.0 | **2.0.0**（bundle 全局） | 新 seed 及后续发布 bundle |
| 薪俸／PA input schema | 1.0.0 | **2.0.0** | 请求体 `schema_version` |
| 其余 5 input schema | 1.0.0 | 1.0.0 不变 | profits／property／stamp×3 |
| 运行时 `session_epoch`/`input_revision` | —— | meta/version 响应附加字段 | **不是** binding/core 字段，不入哈希对象 |
| 七税 prepare 协议 | 三段信封 | ＋`If-Match:"edit-<epoch>-<revision>"` | 显式 G2 delta；其余五税 input/算法不变 |

### 4.2 端点语义（实际 URL；沿用 Annex C C3.1/C3.2）

- `POST /api/v1/calc/prepare`：按税项校验 schema（薪俸／PA 仅 2.0.0；旧 1.0.0 → 422 `E_INPUT_TYPE` field=schema_version，附“旧确认已失效，请重新 prepare＋confirm”，**不迁移**）。**协议增量（Annex 005 §12.1–§12.2）**：唯一 reservation 入口＝SecurityMiddleware 对 prepare 调用**一次** `begin_prepare`（route 不得再递增 revision；middleware/routes 共享同一 ConsentStore，create_app 装配）；顺序＝Host→Origin→session/CSRF→`If-Match` CAS→原子 rev++/失效→大小上限→有界读取→JSON/业务校验→保存前再 CAS。403 不推进/不读 body；缺失/陈旧 If-Match 409 不推进；匹配且声明超限**先推进后 413** `E_INPUT_OVERSIZE`；422/500 不回滚失效并附本操作 ACK。运行时头 `X-Edit-Applied`／`X-Edit-Operation-Epoch`／`X-Edit-Operation-Revision`（仅 Applied=1，不换成最新值）／`X-Edit-Current-Epoch`／`X-Edit-Current-Revision`（409 只读冲突信息，不自动接纳）；全部 no-store；403 不带版本头。本年 needs_input/blocked → 422＋questions/blocked_reason，**无 prepared_id**；仅缺未来组件不阻断本年确认。返回完整 binding（四元组）。
- **原子消费/完成（Annex 005 §12.3）**：`consume_for_execution` 在 registry→store 锁序内冻结 ExecutionContext（canonical bytes/binding/bundle/AvailabilityContext）后才标 consumed；消费后禁止再 `get_prepared`/读 current bundle；引擎用独立工作副本；`complete_execution` 原子保存 record＋consumed 链接（record_id 登记一次，禁止 `confirmation["record_id"]=…`）；编辑后可存历史 record，**`editor_result=current` 仅当当前 revision/binding/相关 guard 仍匹配 context，否则仅 historical**；clear/epoch 变化 → discarded、不 ensure_session（**discarded：HTTP 409 `E_CONFIRMATION_STALE`、AI rejected 同码，且不返回旧 core/税额**）；同 execution 幂等/冲突 409 `E_CONFIRMATION_CONSUMED`；运行时 execution 元数据不入 11 core/4 binding。调用方含 HTTP/AI/getter/clear/失败路径全部迁移。**clear＝epoch+1、rev=0 的 epoch 边界（G2 提案），非 reservation**。
- **编辑队列（与 004 §7/flows 18/19/21 最终语义一致）**：单一 mutation 队列覆盖 invalidate 与 prepare；同标签页仅一个在途，在途编辑合并 generation；**同一 ACTIVE operation＋同一 live epoch 的 Operation ACK 推进内部已确认版本（即使本地 generation 已变），其 stale payload/errors/prepared/result 丢弃且不覆盖最新草稿**；**旧 epoch／已取消 operation／显式重同步后的 ACK 整体忽略**；Current 头只读、不自动接纳、不自动 prepare/重试；跨标签页 409 显式重同步。
- `POST /api/v1/calc/invalidate`（**新增，G2 决定项**）：`{expected_session_epoch, expected_input_revision}`；CAS 成功 200 `{status:"invalidated", session_epoch, input_revision(rev++)}`，删除旧 prepared、使未消费 confirm 与该 sid prepared/granted consent 失效、**records 保留**；不递增全局 consent_epoch；CAS 失败 → 409 `E_CONFIRMATION_STALE` 附当前值；不自动重试。
- `POST /api/v1/calc/confirm`：`prepared_id`＋`canonical_input_hash`＋`expected_binding`＋acknowledge；同一原子操作核对**当前 revision/epoch/hash/schema/tax/target/binding/TTL**后签发能力（ADR-4）。
- **执行端点（7 个，实际 URL）**：`/api/v1/calc/salaries-tax`、`/api/v1/calc/profits-tax`、`/api/v1/calc/property-tax`、`/api/v1/personal-assessment/compare`、`/api/v1/calc/stamp-duty/{property,stock,lease}`（`EXECUTE_TARGETS` 现表；无泛型 execute）。执行**原子消费**：复核 revision/epoch/binding/一致性并重新核对 guard 快照；错目标 → 409 `E_CONFIRMATION_TARGET_MISMATCH` 不消费；规则阻断/不兼容 → `E_RULES_STATE_UNVERIFIABLE` 不消费。
- `POST /api/v1/records/replay`：引擎版本不完全相等 → 409 `E_BUNDLE_INCOMPATIBLE`（7 个目标一致拒绝）；历史查看／下载／打印不受影响。**兼容重放按 Annex 005 §2.2：原 binding/原 bundle＋当前可信 guard；原指针≠当前指针不单独拒绝；engine/hash/schema/profile 严格检查为七目标共同，其余五税保持既有 current-period guard、不扩 next/年度逻辑（§2.2/§12.3）。**
- `GET /api/v1/meta/version|rules|coverage`：v2 版本＋运行时 epoch/revision＋1.3 支持矩阵。

### 4.3 错误分类（新增／沿用）

- 沿用 C3.7 全部码。**422 形状**：`{"error":{code,message_zh,field,details,binding}, questions[], status}`；`questions[]` 含 `{field,year,claim_id?,subject_id?,question_zh}`；needs_input/blocked 均**无 prepared_id**。
- `E_INPUT_CONTRADICTORY` 扩展至 ledger 冲突／来源矛盾／假设与显式事实冲突；`E_CONFIRMATION_STALE` 用于 invalidate CAS/confirm 版本不符（409）；`E_RULES_STATE_UNVERIFIABLE` 扩展至旧/不兼容 bundle 拒算；未知资格保持 `E_ELIGIBILITY_UNKNOWN`＋questions。
- 迟到的 AI 响应在处理 tool_calls 前重新核验 epoch/revision/consent；过期整轮丢弃；迟到本地计算可保存原绑定 record，但不得成为当前 editor 结果。

### 4.4 AI 工具契约（Annex 005 §16）

- `propose_input`／`validate_input` 参数 `{"input":{tax_type,schema_version,input}}`，内部为**完整 HTTP 业务信封**；全 7 税映射固定（2 税→2.0.0，其余→1.0.0）；同一校验器＋语义预检；不回声、不创建能力、不补默认资格。成功 result 恰含 `proposal/canonical_input_hash/expected_binding/status:"checked"/questions/missing_components`；拒绝 error 映射 AppError 精确字段。
- `calculate_confirmed` 消费前执行与 HTTP 相同的复核，并与 HTTP 共用 §12.3 的 ExecutionContext 快照/原子完成/幂等语义；`read_result` 本地返回最小保存结果（`record_id/tax_type/binding/status/amounts/blocked_reason/missing_components/unsupported/pending_verification/steps`，steps 仅 `scenario/selection/next_year`，不读 raw facts/decision），**不自动外发**；如需发送结果摘要须显式选取＋新预览＋新 consent。无自动出站重发；不新增隐私/信任边界能力。

## 5. 核心逻辑设计

### 5.1 共享解析器

`resolve_allowances(context, year, scenario, rules) -> AllowanceResolution`：`assessment_totals, claim_decisions[], deduction_decisions[], issues[]`；claim decision＝`{claim_id, subject_id, claimant_id, status, amount, reason_codes[], year, rule_refs[]}`，`status ∈ {allowed, ineligible, needs_input, manual_handoff, unsupported}`；四态不合并。deduction decision 逐条声明 requested／allowed／not_granted_mutex／unresolved（requested 正数 ≠ allowed；Annex 005 §6.2）。API 校验器只查结构／标量／引用完整性，不复制资格语义。

### 5.2 逐项资格（要点；谓词／金额按 Annex 005 §7／§15.2）

- 子女：三替代路径＋同一日期交集；三层金额分别 `min(Σbase,cap_(1))＋min(Σ(1A),cap_(1A))＋min(Σ(1B),cap_(1B))`（独立 caps；24/25–25/26 (1B) 已核不适用）。
- 父母／祖父母：高档/低档量化词＋供养双路径独立于年龄档；全年同住额外额独立 claim。
- 兄弟姊妹／单亲／本人伤残／伤残受养人：Annex 005 §7；s.31A entitlement 不要求基本 claim；DAA 只问资格不问领取。
- 互斥与叠加按 §8 ledger；争议／Commissioner／死亡年度／复杂来源 = 可提交、可定位 manual。

### 5.3 夫妻薪俸税：两遍条件式联合与归属（s.10／s.29；Annex 005 §9.1）

- **两遍流程（无递归/固定点）**：第一遍按分别路径解析各自 Part4A/Part5、child 提名与 grant，计算每人分别 final，得 A＝任一方 Part4A＋Part5＞其 NAI、B前提＝双方分别 NCI>0；第二遍在“假设 joint”内部上下文解析一份 married 与依赖它的 spouse DDA，算 conditional joint final，B＝B前提且分别合计严格大于该值；前置（普通婚姻窗口＋双方 AI＋选择一致）且 (A OR B) 才接纳 joint，否则 ineligible/unresolved。**(a) 成立不得因 (b) 不成立排除**；等号不满足。
- **提名字段**：`Year.assessment.actual.salary_joint_nomination?`／`hypothetical_salary_joint_nomination?`＝`{status:"none"|"named"|"unknown"|"disputed", person_id:pID|null}`（Annex 005 §4.10）；与 `household.child_nomination` **严格不同**。(a) 责任人服务器派生；提名字段与派生责任人冲突 → `E_INPUT_CONTRADICTORY`；需归属而缺失/unknown → needs_input；disputed → manual；不得从 child nominee、收入较高者或 self 推断。
- **归属矩阵**：Annex 005 §9.1.1（basic/married 跨人互斥、child DDA 无基本 claim 时仍随 child 整组 nominee、spouse DDA 依 s.29(2) 责任人、精确评估单位）。s.10(2)(a) 责任人不能唯一确定时**仅该 joint 归属决定 unresolved，不清空独立已知 separate 结果**。
- 金额逐人列示＋明确合计；婚姻缺失/unknown 且涉判定 → needs_input，不默认 single；条件式检查/金额只进 `candidate_checks` 非权威 trace，不进 scenario/selected/best（Annex 005 §14）。
- 具名 T3 计划（手算未执行）：`test_002_joint_spouse_dda_conditional_admission_no_cycle`（500k/200k、MPF 18k：38,500／47,000／34,250）＋反向 nominee 负例。

### 5.4 PA 与两税基数（AC-8；真值表 Annex 005 §9.2–§9.3）

- Part 5 只降累进基数（s.42A）；标准径基数不含 Part 5（薪俸 NAI−Part4A；PA s.43(1A) 减少后总入息）；两者不互借。
- 候选：`candidates` 须显式、非空、不隐式添加；**`no_pa` 不是自动候选**（仅当显式列入才比较）；每候选派生只读内部 `EffectiveAssessment`（Annex 005 §9.3：`salary_mode: separate|joint|absorbed_by_pa`＋`pa_by_person:{pID:{mode:"none"|"individual"|"joint"|"unknown", date?:Date}}`＋responsible/assumed；**不是新 wire 字段**）——salary 候选**复制每人 actual PA 条目不变**；noPa 每人 none；paSelf self individual（参考日）/spouse none；paJoint `absorbed_by_pa`＋双方 joint，**不虚构 salary_joint_elected/assessed=true**；`pa_self` 的 s.29 判断须看到 self 已 individual PA；**actual no_pa＋joint salaries 本阶段 unsupported，不静默拆成 separate**。**`pa_self`＋spouse no_pa 混合编排为 G2 决定项**；跨 PA/no_pa 共享单位无已批分配时仅该 mixed 候选 `unsupported`（`CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED`），作为**未决竞争者**参与 best；不新增 PA 组合/分配公式/来源 flag/默认值，也不禁用 all-PA/all-no_pa/普通合伙。unknown 资格 → 该候选 needs_input，不伪造合法候选。
- joint PA 分摊按既有约定（先宽减后精确比例、ΣR=0 不除法、无个人整元）；PA 恒 partial、balance null。

### 5.5 结果语义、下一年度与可用性

- 状态四态＋六金额键不变；组件适用按 Annex 005 §10（PA 恒 partial；salary partial → balance null；blocked 六键全 null）；`best` 仅依候选本年 final＋资格，unknown 竞争者 → null；显式选中已知候选可保留 final。
- 先完成本年 final；目标年缺规则/事实/guard → `next_provisional=null`、partial、missing 说明，**不回退本年**；目标年规则可用时**重新解析**（年龄档、出生额外额、独立 cap、资格与方案合法性），不复用本年对象。
- 下一年模型（Annex 005 §11）：稳定复用仅 ID/birth_date；收入/MPF 同额投射仅经 `same_income`/`same_mpf` 具名假设；显式目标事实与假设异额 → `E_INPUT_CONTRADICTORY`；禁止复制 role／选择／claims／教育 TF；缺失即 next unavailable、本年 final 保留。`AvailabilityContext` 为显式参数进 HTTP 预检／AI／纯引擎（引擎内无网络，缺 context 不放行）。

## 6. 安全与隐私

沿用 C5/C4 不变量：prepare→confirm→execute、会话／CSRF、目标与版本绑定、单次消费、AI 单次外发同意；输入最小化（不收 HKID／姓名／医疗详情）；错误与记录不泄密；不得以聊天绕过校验、确认或授权；无自动出站 fact 上传／工具自动重发。本 CR 新增失效/CAS 协议不构成新信任边界；**不新增隐私/信任能力**；不触碰 `.env` 与运行中服务。

## 7. 文件布局与运行／验证命令

```text
新增：app/engines/allowances.py  app/rules/bundles/allowances.py
改动：app/engines/{salaries,personal_assessment}.py  app/rules/{bundle,store}.py  app/config.py
      app/api/{validation,routes,consent}.py  app/web/{records,security,coverage_data}.py
      app/main.py（真空库判定＋STOP_AUTO_OBSERVATIONS＋启动 seam）  app/ai/tools.py
      app/updater/{classifier,scheduler,reference,testgate,publisher}.py
      app/web/templates/*（frontend lane，依 designer 004/Annex 005 §17）
      scripts/official_calc_compare/{local_engine,run_all}.py  scripts/generate_validation_report.py
      scripts/check_req_traceability.py  pyproject.toml（版本 0.2.0；依赖不变）
      README/运行配置文档（版本与责任说明；不新增依赖）
测试：tests/{engines,api,ai,updater,ui,e2e,unit,acceptance}/test_002_*.py（由 test-writer 创建；命名见 §9.4／006 §5）
不动：001 Approved/Implemented 契约、designer 004、五个非 2 税引擎与算法、CI 工作流、tests/fixtures/official/* 原 bytes
```

命令（本机；**每 run 唯一 basetemp＝时间戳＋GUID**；命令与 §9.4／006 §5 拟建文件一一对应；CI 仍 `python -m pytest -q` 不扩张）：

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'; $guid = [guid]::NewGuid().ToString('N').Substring(0,8)
$base = "C:\Users\elvis\AppData\Local\Temp\opencode\pytest-002-$stamp-$guid"
# P1（解析器／claims／三年度矩阵／夫妻／PA 方案／下一年／结果语义＋存储兼容）
.venv\Scripts\python -m pytest tests/engines/test_002_claims_tristate.py tests/engines/test_002_children.py tests/engines/test_002_parents.py tests/engines/test_002_other_allowances.py tests/engines/test_002_claim_ledger.py tests/engines/test_002_consume_semantics.py tests/engines/test_002_salaries_couples.py tests/engines/test_002_pa_allowances.py tests/engines/test_002_next_year.py tests/engines/test_002_result_semantics.py tests/unit/test_002_store_compat.py tests/api/test_002_schema_versions.py tests/api/test_002_confirmations.py tests/api/test_002_assessment_dependencies.py tests/api/test_002_salaries_couples_api.py -q --basetemp="$base\p1"
# P2（AI／UI 结构／记录，含受影响调用方全目录回归）
.venv\Scripts\python -m pytest tests/api tests/ai tests/ui -q --basetemp="$base\p2"
# P3（更新／发布／单测＋真实 Chromium e2e＋全量；缺失 Chromium＝blocked，不 skip 计通过）
.venv\Scripts\python -m pytest tests/updater tests/unit -q --basetemp="$base\p3a"
.venv\Scripts\python -m pytest tests/e2e -q --basetemp="$base\p3b"
.venv\Scripts\python -m pytest -q --basetemp="$base\p3c"
# 追溯（001 接口保留；002 命名空间已实现，P3-W2）：
.venv\Scripts\python scripts/check_req_traceability.py --spec 002 --require-002 1,2,3,4,5,6,7,8,9,10,11,12,13
# 报告（owner=002 只写 EV/reports；默认 001 历史路径不变；禁止覆盖 001 报告）：
.venv\Scripts\python scripts/generate_validation_report.py --owner 002 --output <EV>\reports\002-engine-io-report.md
# 固定束离线核验（默认不联网；live 需显式 --live）：
.venv\Scripts\python -m scripts.official_calc_compare.run_all --report <EV>\reports\002-fixed-bundle.md
```

## 8. 编号需求 REQ-1..REQ-13（与 AC-1..AC-13 一一对应）

| REQ | AC | 需求（子条款含理由） |
| --- | --- | --- |
| REQ-1 | AC-1 | 支持范围逐项区分「支持计算／缺资料待确认／范围外需人工」，API、网页、聊天文案一致；阶段 2／3 不得称已支持或“完整最优”（REQ-1.1：`support_level ∈ {computable,needs_input,manual_handoff}`＋`phase`＋`limits_zh` 数据驱动；**聊天经既有 `chat_coverage_guidance(items)` 本地视图消费同一 `coverage_items()` 源**，不新增端点/工具/外发键，理由＝防文案与实现漂移）。 |
| REQ-2 | AC-2 | 资格事实原始 Tri＋缺失≠false≠"0"；事实独立于 claims、claims 仅意图（REQ-2.1）；时间条件按 Annex 005 §7 逐条款 ANY/ALL 谓词，异时点/全量求交不得拼资格（REQ-2.2）；manual 边界经 `special_situation`/`manual_issues` 提交定位、不整类禁算（REQ-2.3）；NAI/AI/全来源三口径分离，正 NAI×AI=false 矛盾、unknown 不自动裁定（REQ-2.4）。 |
| REQ-3 | AC-3 | 子女 <18／18–25 全日制教育／18+ 伤残不能工作三**替代**路径（SAMEPOINT 同时性）；**不添加一般居港条件，删除旧“居港 unknown 追问”断言**；出生年（1A）与出生次年（1B）独立金额与独立 cap；半开 TF 支持部分年度（新生儿/出生年案例）。 |
| REQ-4 | AC-4 | 父母／祖父母：`ANY(居港)`＋高档（60+ 或 <60 且 DAA 资格）或 55–59 档（`ALL(NOT DAA)`，unknown 不默认 false）；供养＝年度现金 ≥12,000 **或**连续 6 个日历月非足值同住，**独立于年龄档**；全年同住额外额独立 claim；月末边界/死亡年度＝窄 manual；DAA 有资格不要求已领取。 |
| REQ-5 | AC-5 | 兄弟姊妹（维护/未婚/年龄路径＋`ANY(sole_or_predominant_care)`，汇钱不足）；单亲（≤1 次、child **entitlement** 不要求基本 claim、排除 `ANY(married AND NOT apart)`）；本人伤残（s.28A）；伤残受养人（s.31A entitled 独立于基本 claim/扣除项，spouse 路径含分支 A/joint salaries/joint PA，s.26D 原始事实可替代基础，不误设不可叠加）。 |
| REQ-6 | AC-6 | 同一方案内 `(kind,claimant,subject)` ledger＋类互斥（parent＋grandparent、child＋sibling）、合法叠加（child＋disabled、parent＋cohab-extra）、父母额与 s.26D 获扣除同人互斥（requested≠allowed，逐条 decision）；整组提名对账；争议转人工；PA/no-PA 各自 ledger 复用不算重复。 |
| REQ-7 | AC-7 | 夫妻收入/MPF/婚姻/分居/选择事实经 API 与网页可录入、校验、到达引擎；按 Annex 005 §9.1 真值表与 **§9.1.2 两遍条件式流程**处理（分支 A、S6 已核普通婚姻窗口、S7 窄 manual/needs_input、非法候选列原因）；**actual/hypothetical 独立 salary-joint 提名字段**（与 child nomination 分离，责任人 (a) 派生/冲突报错、(b) 显式无默认）；婚姻缺失不默认 single；金额逐人列示；实际/hypothetical 选择语义分离；条件式金额只进非权威 `candidate_checks`；**同一 YA 已注册、排序、非重叠的顺序配偶变化经结构校验后走 S7 manual/unresolved**（locator/reason 为计算标签，不新增 wire 码）；`basis/candidates/selection` 无条件、`actual` 成员按活动消费者条件（Annex 005 §4.10/§9.1）。 |
| REQ-8 | AC-8 | Part 5 正确接入有限 PA/no-PA 方案；`pa_self`/`pa_joint`/`no_pa` 按 s.41(1)/(1A)/(1B) 原始事实判定；**每候选有效选择/actors/单位覆盖表**（Annex 005 §9.3；每人 actual PA 条目**稀疏复制自 actual.pa 的已提交条目、缺键＝缺席不补默认（不补 none/unknown）**、noPa=separate、actual no_pa＋joint salaries unsupported）；跨 PA/no_pa 共享单位无已批分配时仅该 mixed 候选 unsupported/unresolved（不作 ineligible）；all-PA/all-no_pa/普通合伙保留；只降累进基数；申索权与互斥按 REQ-6；混合 `pa_self`＋spouse no_pa 为 **G2 决定项**；取整按真实分支（ADR-6）并入 trace。 |
| REQ-9 | AC-9 | 键年＝目标＋紧邻下一年；缺目标规则/事实/guard → 不回退，`next_provisional=null`、partial＋说明，本年 final 保留；有规则时重新解析；假设仅 `same_income`/`same_mpf`，异额冲突拒绝；稳定复用仅 ID/birth_date，不复制 role/选择/claims；`AvailabilityContext` 显式进预检/AI/引擎（无网络、无默认放行）。 |
| REQ-10 | AC-10 | 六金额键与组件状态按 Annex 005 §10：salary 任一 partial → balance null；PA 恒 partial、balance null＋payment_comparison 范围外；`best` 仅依候选本年 final＋资格（独立于 paid/next），unknown/unresolved 竞争者 → null，显式选中已知候选可保留 final；blocked 六键全 null；网页/AI/步骤/打印导出一致，null≠0；**PA/salary partial 仍须执行完整 tagged steps 检查（验收不得 complete-only 跳过；其余五税口径不变）**。 |
| REQ-11 | AC-11 | 渐进披露表单与聊天可完成受支持流程；缺资料按 questions 定位；**失效协议（Annex 005 §12）**：唯一 reservation 入口（SecurityMiddleware `begin_prepare` 一次；route 不双递增；共享 ConsentStore）、403/409/413/422 固定失败表、Operation ACK 头（本操作 ≠ 当前版本）、单一 mutation 队列覆盖 invalidate 与 prepare、迟到响应丢弃、clear＝epoch+1/rev0 边界；confirm/execute **原子消费 ExecutionContext 快照**与原子完成/幂等/409 冲突；维持 CSRF／会话／目标绑定／单次消费／AI 单次外发同意（REQ-11.1）。 |
| REQ-12 | AC-12 | 版本与规则证据：engine 0.2.0／bundle 2.0.0／2 税 input 2.0.0（其余不变，但共享 If-Match 协议 delta 须显式呈报）；固定 profile＋digest 只覆盖冻结语义（语义变化同步 P 标识/重算 digest，不增哈希字段/第五 binding）；六条旧路径 stop-auto 仅观察、无新 auto slot；**observer pending 所有权/sticky 状态机/受控 Resolution（Annex 005 §15.5；普通通知同步不得清除；MPF 三年度分别 pending）**；8 条不受影响路径保留；报告 `purpose` seed/update＋nullable baseline＋required 重算＋empty-CAS；无 in-scope witness 记 `coverage_gap`（stock effective_from）而非伪 fixture；旧记录/binding/导出不静默改写；7 目标 replay 拒绝；发布绑定 baseline/候选/证据/manifests/required case 并在事务内复核；五入口守卫与空库五态；**replay 原 binding/原 bundle＋当前 guard 的显式 context 闭包与失败优先级（Annex 005 §2.2）**；reference/report 显式 case context 与逐 case 适配来源。 |
| REQ-13 | AC-13 | Gate 2 后 test-writer 先写独立来源 Red（本规格授权 Red cases/fixtures，**不等于新 auto slot 放行**）并确认正确失败原因，executor 最小实现；覆盖 §9.4 与 006 §4–§5 清单；历史断言逐节点映射新 REQ（禁 blanket 改 expected、原 fixture bytes 不改、旧文件名 REQ 标记不计行为覆盖）；完整套件＋diff/untracked 审阅＋独立 acceptor ACCEPTED；未运行/受阻不算通过。 |

## 9. 测试计划

### 9.1 框架与分层

pytest（unit／engines／api／ai／updater／ui／e2e／acceptance＋Hypothesis 既有）；UI 分“结构响应”与真实 Chromium e2e；金额链路零浮点；每 REQ 至少一个 Red 并含普通／边界／unknown／API／browser／version／update 类别（§9.4/006 §5）。

### 9.2 fixture 溯源

沿用 Annex C C8.2／001 REQ-15：T1 官方 fixture 与 T3 独立手算（可审计）分离；期望值不得取自生产 resolver；官方原始 fixture／source bytes 不改；新增期望标注来源（法条行号／T1-observed／T2＋手算／T3 算式注释）。PAM39/PAM37 属 T2 候选锚＋法例手算，不得改标 T1-observed（Annex D D12）。**旧文件名中的 `REQ-n` 标记只属 001 命名空间 provenance，不构成 002 行为覆盖证据**（006 §4.1）。

### 9.3 AC → REQ 映射

一一对应：AC-n ↔ REQ-n（n=1..13）；子条款见 §8；无跨 AC 的 REQ。

### 9.4 REQ → 命名测试映射（计划，未创建；完整清单与 27 文件级处置见 006 §4–§5；命令见 §7）

| REQ | 拟建文件::测试名（要点断言） |
| --- | --- |
| 1 | tests/api/test_002_coverage_labels.py::test_002_coverage_labels_support_gap_manual（computable/needs_input/manual＋phase＋limits_zh）；tests/ui/test_002_coverage_ui.py::test_002_pa_api_only_no_full_workbench |
| 2 | tests/engines/test_002_claims_tristate.py::test_002_unknown_fact_needs_input_null_amounts；::test_002_false_missing_zero_distinct；::test_002_period_bounded_within_year；::test_002_disjoint_paths_not_intersected；::test_002_nai_positive_ai_false_contradictory；::test_002_ai_unknown_salary_couple_needs_input；tests/api/test_002_schema_versions.py::test_002_unknown_422_questions_no_prepared_id |
| 3 | tests/engines/test_002_children.py::test_002_child_under18_no_residence_condition；::test_002_child_18_25_education_tf_window；::test_002_child_18_disabled_incapacity_alternative_path；::test_002_child_caps_separate_three_groups_2026_27；::test_002_child_1b_verified_not_applicable_2024_25_2025_26；::test_002_twins_same_birth_two_ids_legal；::test_002_newborn_part_year_samepoint |
| 4 | tests/engines/test_002_parents.py::test_002_parent_60plus_basic_extra_separate；::test_002_parent_under60_daa_eligible_high_tier；::test_002_parent_55_59_daa_ineligible_all_not_anytime_required_unknown_not_false；::test_002_parent_six_calendar_month_cohabitation_non_full_consideration；::test_002_parent_cash_threshold_12000_annual_total；::test_002_parent_maintenance_independent_of_age_window；::test_002_parent_daa_eligibility_not_receipt；::test_002_parent_extra_full_year_only_claim；::test_002_parent_month_end_case_manual_not_invented |
| 5 | tests/engines/test_002_other_allowances.py::test_002_sibling_care_not_money_only；::test_002_sibling_adult_paths_alternative；::test_002_single_parent_once_not_per_child_entitlement_without_base_claim；::test_002_single_parent_exclusion_married_not_apart_anytime；::test_002_s28a_personal_disability_eligibility_not_receipt；::test_002_s31a_entitlement_without_base_claim；::test_002_s31a_spouse_subject_paths；::test_002_s26d_raw_facts_alternative_basis_no_auto_deduction |
| 6 | tests/engines/test_002_claim_ledger.py::test_002_duplicate_key_rejected；::test_002_parent_grandparent_mutex；::test_002_child_sibling_mutex；::test_002_child_plus_disabled_stack_legal；::test_002_parent_plus_cohab_extra_stack_legal；::test_002_26d_granted_vs_parent_allowance_mutex；::test_002_enbloc_nomination_reconciles；::test_002_pa_no_pa_reuse_not_duplicate；::test_002_special_situation_manual_handoff_locator |
| 7 | tests/engines/test_002_salaries_couples.py::test_002_s29_branch_a_no_joint_required；::test_002_both_ai_no_joint_each_basic；::test_002_joint_legal_b_separate_exceeds_joint；::test_002_joint_legal_a_without_b_excludes_nothing；::test_002_joint_illegal_candidate_ineligible_reason；::test_002_salary_separation_window_not_global_ineligible；::test_002_clean_marriage_transition_s10_5；::test_002_partial_child_facts_do_not_trigger_household_manual；::test_002_marriage_missing_not_default_single；::test_002_joint_spouse_dda_conditional_admission_no_cycle（T3 手算）；::test_002_reverse_salary_nominee_no_wrong_grant；tests/api/test_002_salaries_couples_api.py::test_002_couple_fields_reach_engine；::test_002_joint_unknown_asks_no_optimal；::test_002_spouse_separate_pa_election_fact；::test_002_actual_hypothetical_nomination_wire_fields |
| 8 | tests/engines/test_002_pa_allowances.py::test_002_part5_lowers_progressive_not_standard_base；::test_002_salary_std_base_unchanged；::test_002_pa_self_spouse_no_pa_mixed_g2_proposal_tagged；::test_002_rounding_branches_in_trace_per_annex_d（含 no_pa salary CompTP 子分支与 rebate floor/ceil 区分，无虚构链）；::test_002_s41_1b_blocks_separate_pa_after_joint_salaries；::test_002_married_not_auto_joint_pa；tests/acceptance/test_002_pa_allowance_e2e.py::test_002_pa_no_pa_same_facts_two_ledgers |
| 9 | tests/engines/test_002_next_year.py::test_002_missing_target_rules_null_partial_no_fallback；::test_002_known_target_reparse_birth_ages_caps；::test_002_assumptions_only_same_income_same_mpf；::test_002_assumption_conflict_explicit_rejected；::test_002_no_copy_role_education_selection_claims_to_next；::test_002_missing_next_fact_keeps_current_final；::test_002_salary_partial_balance_null；::test_002_availability_context_no_default_permission |
| 10 | tests/engines/test_002_result_semantics.py::test_002_six_keys_null_never_zero；::test_002_pa_always_partial_balance_null_payment_out_of_scope；::test_002_salary_balance_null_when_paid_or_partial；::test_002_best_independent_of_paid_unknown_competitor_blocks；::test_002_explicit_selected_known_retains_final；::test_002_blocked_all_six_null_c3；tests/ui/test_002_result_null_not_zero.py::test_002_ui_null_unestimated |
| 11 | tests/api/test_002_confirmations.py::test_002_invalidate_cas_409_current；::test_002_invalidate_revokes_pending_keeps_records；::test_002_prepare_if_match_required_and_rotates；::test_002_prepare_invalid_body_still_invalidates_no_capability；::test_002_confirm_atomic_revision_binding；::test_002_wrong_target_409_not_consumed；tests/ai/test_002_ai_contract.py::test_002_full_envelope_inside_arguments_input_all7；::test_002_success_result_closed_keys；::test_002_rejection_maps_apperror_fields；::test_002_read_result_minimal_saved_steps_no_raw_facts；::test_002_no_auto_resend_new_consent_for_results；tests/ui/test_002_household_form.py::test_002_edit_invalidates_before_ack；tests/e2e/test_002_household_browser.py::test_002_couple_input_confirm_execute_e2e；::test_002_question_locator_focus；::test_002_edit_invalidates_and_history_kept |
| 12 | tests/api/test_002_schema_versions.py::test_002_v2_accepted_v1_rejected_reconfirm；::test_002_five_other_inputs_keep_v1_but_prepare_if_match_shared；tests/unit/test_002_store_compat.py::test_002_true_empty_only_seed；::test_002_orphan_bundles_fail_closed_no_reseed；::test_002_dangling_pointer_fail_closed；::test_002_hash_corruption_fail_closed；::test_002_incompatible_bundle_blocks_calc_replays_kept；tests/updater/test_002_manifest_mapping.py::test_002_six_old_paths_stop_auto_observer_only；::test_002_stop_auto_change_quarantines_known_period；::test_002_observer_extract_cannot_enter_classifier；::test_002_eight_unchanged_paths_affected_cases_planned；::test_002_semantic_digest_profile_frozen_dual_check；::test_002_gate2_authorizes_red_cases_not_auto_slots；tests/ui/test_002_records.py::test_002_all_7_replays_refused_engine_mismatch |
| 13 | tests/unit/test_002_traceability_namespace.py::test_002_namespace_mutex_and_named_mapping（旧文件名 REQ 标记不计行为覆盖）；tests/acceptance/test_002_independent_expectations.py::test_002_hand_derived_not_producer_oracle；tests/acceptance/test_002_stage1_matrix.py::test_002_full_applicable_suite_matrix；tests/e2e/test_002_browser_isolation.py::test_002_start_scheduler_seam_replaced_before_create_app（真实出站计数 0、唯一 DB；缺 Chromium 记 blocked 不计通过） |

**第四稿新增具名 Red（映射见 006 §5；全部 PLANNED）**：B1 `test_002_joint_spouse_dda_conditional_admission_no_cycle`＋反向 nominee 负例；B2 `test_002_salary_separation_window_not_global_ineligible`／`test_002_clean_marriage_transition_s10_5`／`test_002_partial_child_facts_do_not_trigger_household_manual`（**替换旧 `test_002_living_apart_event_blocks_joint`**）；B3 `test_002_candidate_effective_salary_pa_matrix`／`test_002_effective_pa_entry_closed_map_no_scalar_collapse`／`test_002_actual_no_pa_joint_salary_not_silently_split`／`test_002_pa_self_effective_election_changes_spouse_s29`／`test_002_mixed_shared_unit_not_double_counted`／`test_002_all_pa_all_no_pa_ordinary_partnership_preserved`；R3#4 `test_002_home_status_tf_prior_year_care_period`／`test_002_item_type_unsupported_positive_rejected_zero_accepted`／`test_002_erce_unknown_missing_still_needs`／`test_002_hk_presence_days_union_of_explicit_years`；REQ-10 `test_002_negative_salary_balance_full_context_refund`（全组件已知＋负 balance 退税；PA 仍 null）；协议/消费 `test_002_consume_snapshot_survives_edit_before_compute` 等（006 §5.5）＋`test_002_prepare_matching_oversize_advances_and_acks_413` 等 ACK/413 八测试（**断言 ASGI receive 次数**）；observer 十二测试（006 §5.6，含 `test_002_resolution_baseline_change_cannot_reuse`）；UI 七场景（006 §6.7）；证明负例十四（006 §7.2）。

**第五稿新增具名 Red（PLANNED；Advisory §5 全量；映射见 006 §5.8）**：`tests/ui/test_002_records.py::test_002_replay_explicit_context_preserves_pinned_bundle`／`::test_002_replay_missing_or_denied_context_never_defaults_available`（**扩充**既有 7 目标旧引擎拒绝计划节点，不造重名）；`tests/updater/test_002_reference_context.py::test_002_reference_static_candidate_explicit_context_and_v2_facts`／`::test_002_reference_missing_evidence_not_auto_qualified`／`::test_002_case_context_cannot_admit_public_request`；`tests/unit/test_002_report_context.py::test_002_salary_pa_direct_report_calls_require_bound_context`；`tests/api/test_002_assessment_dependencies.py::test_002_single_salary_api_ui_without_inactive_assessment_fields`／`::test_002_assessment_fields_follow_active_path_without_fabricated_defaults`；`tests/engines/test_002_salaries_couples.py::test_002_sequential_spouse_change_reaches_s7_manual`／`::test_002_conflicting_spouse_identity_remains_contradictory`；`tests/ai/test_002_chat_coverage.py::test_002_chat_coverage_consumes_shared_source_and_preserves_unknown`／`::test_002_chat_never_claims_stage2_stage3_or_pa_web_support`；`tests/e2e/test_002_household_browser.py::test_002_single_salary_no_inactive_assessment_controls`／`tests/e2e/test_002_chat_browser.py::test_002_chat_local_coverage_offline_no_outbound`（preview/grant/chat 调用均 0，后续发送仍走独立 consent）。**Designer 五新 flow 25–29**：25 e2e `test_002_single_salary_no_inactive_assessment_controls`（REQ-7/11）；26 `tests/ui/test_002_household_form.py::test_ui_conditional_assessment_unknown_keeps_known_separate`（REQ-2/7/8；活动 unknown 追问、已知 separate 保留、best=null）；27 e2e `test_002_same_ya_sequential_spouse_history_manual`（REQ-2/7；locator `years.2025_26.household.marriage`/subject_id=p1；不产家庭总税）；28 AI `tests/ai/test_002_chat_coverage.py` 两节点（REQ-1/11）；29 e2e `test_002_chat_local_coverage_offline_no_outbound`（REQ-1/11；locator `chat-local-coverage-toggle`/`chat-local-coverage-panel`，preview/grant/chat/outbound 0；后续显式发送仍走冻结 preview/单次 grant/一次 send）；旧 24 flow/7 queue 场景保留。

**参数化正反边界清单**（并入上述文件）：三年度全表、18/25/55/60 年龄档、现金门槛 12,000（年合计）、连续 6 日历月、合法叠加（child＋disabled、parent＋extra）、未实际 claim 的 s.31A entitlement、配偶/s.26D 路径、未来规则/事实/guard、非法联合候选（S5/P3）、保存 trace 与 HTTP/AI 一致、salary partial balance null、PA 恒 partial、qualified zero-reduced（ΣR=0）与 zero taxable income 分离、PA 完整/零 joint 收入且合法来源案例、部分年度 newNode/出生年同点资格、月末窄边界 manual。

### 9.5 历史断言闭包矩阵（摘要；**完整逐节点处置见 006 §4**；禁 blanket 改 expected；原 fixture bytes 不改）

| 历史测试（node） | 处置与原因 | 新 REQ |
| --- | --- | --- |
| `tests/engines/test_salaries_engine.py::test_salaries_pam39_q1_final_940`；`::test_salaries_floor_convention_three_years_official`；`::test_salaries_pam39_pst_2026_27_35890` | 保留数值/取整覆盖；迁 v2 facts/schema；PST 段 `next_provisional` 改为 2027/28 缺规则 → null/partial | REQ-8/9/13 |
| `::test_salaries_mpf_mandatory_deduction` | 单人 MPF 保留；joint 段现属**非法联合候选（分别合计 35,860＜联合 51,420，s.10(2)(a)(b) 均不成立）——必须修而非换信封**：替换为 (b) 成立或 (a) 成立的**合法 10-case** | REQ-7 |
| `::test_salaries_unknown_eligibility_questions` | 婚姻 unknown 保留；**子女居港断言删除**（s.31 无一般居港；REQ-3，不弱化为不提问也算） | REQ-2/3 |
| `tests/engines/test_personal_assessment.py::test_pa_q32_example_9920_vs_28800` | 数值/best 保留；`complete`→partial（PA 恒 partial）；配偶无薪俸 AI 边界须显式事实 | REQ-8/10 |
| `::test_pa_compare_after_rebate_not_before` | 宽减后比较保留；“缺 paid 无 best”替换为 best 独立于 paid | REQ-10 |
| `::test_pa_joint_output_total_authoritative_no_per_person_whole`；`::test_pa_pam37_example4_joint_rebate_then_ratio_apportion_internal_fractions` | 分摊不变量保留；**零收入 joint 案例不得虚构 s.41(1A) 资格**——改为有合法来源存在事实且 zero-reduced 的边界 | REQ-7/8 |
| `::test_pa_interest_cap_per_property_nav_share`；`::test_pa_standard_cap_pa_reduced_income_not_salary_reuse`；`::test_pa_unknown_qualification_no_optimal_claim`；`::test_missing_pst_partial_no_total_balance`；`::test_pa_duplicate_claim_blocked` | 数值/状态保留并迁 v2；缺失 PST 的 next 语义按 REQ-9；重复申索扩展为 ledger 矩阵 | REQ-2/6/8/9/10 |
| `tests/acceptance/test_acceptance_dependent_parents.py::test_acceptance_dependent_parents_allowance_computed` | parent 拆两路径＋逐条款量词；年齢不再年度末；缺 next → partial | REQ-4/9 |
| `tests/acceptance/test_acceptance_tax_types_e2e.py::test_acceptance_all_approved_tax_types_end_to_end` | salary＋PA 双双迁 v2/状态；其余五目标数值行为与页面路由保留 | REQ-10/11/12 |
| `tests/api/test_api_input_three_states.py`（4 节点）；`tests/api/test_api_period_and_binding.py`（2 节点＋fixture）；`tests/api/{test_api_headers,test_api_security,test_api_coverage}.py` | 三态/年份 pin 保留；schema 升 2.0.0＋If-Match；coverage 随 §1.3 词汇更新 | REQ-1/2/11/12 |
| `tests/ui/{test_ui_confirm_flow,test_ui_consent,test_ui_money_strings,test_ui_pages_structure,test_ui_session_privacy,test_ui_update}.py`；`tests/ui/test_ui_chat.py`（6 节点） | 结构/安全/状态断言保留；fixture 与字段路径迁 v2；`propose_input` 结果改契约 proposal；页面结构断言不动（designer 意图） | REQ-11（未改动节点＝002-N/A；逐节点映射见 006 §4.3） |
| `tests/ai/*`（6 文件） | 出站/同意/工具门全部保留；`arguments.input` 改完整信封；`read_result` 改最小保存结果 | REQ-11/13 |
| `tests/updater/*`（9 文件） | 管线机制保留；六旧路径 stop-auto 仅观察＋classifier 阻断新/旧共享路径为**新增 guard case**；fake bundle 保持 fake | REQ-12 |
| `tests/acceptance/{bundle_and_readme,bundle_digests_round3,production_wiring}` | seed/README/装配期望随 v2 同步、逐项映射；不改自动放行 | REQ-12/13 |
| `tests/engines/stamps/*`、`test_profits_engine`、`test_property_engine`、`test_money_*`、`test_hashing_*` | 非两税数值/哈希：**数值断言全部保留**；If-Match/revision 由 prepare 请求体站点适配（原列 7 处；grep 实测 ≥9 处，见 006 §4.1） | 002-N/A（001 原样；helper 适配属 REQ-11） |
| `tests/fixtures/official/snapshots/*`（.gz） | **原 bytes 不改**；SourceSha 渲染为 YAML 展示（非 raw HTTP 回放） | REQ-12/13 |

## 10. 里程碑与阶段映射（不重排 M1..M5，不重排路线图阶段 2/3）

| 里程碑 | 内容 | 映射 | 写入所有权（唯一负责人） | 命令 |
| --- | --- | --- | --- | --- |
| M0 | 本文＋Annex 005＋006＋证据档 → plan-reviewer 复审／councillor×3＋council／oracle → 用户 Gate 2 | **P0（文档）** | 后备执行者（仅文档） | 文档 lint（§13） |
| M1 | Red：共享解析器／claims／三年度矩阵／夫妻与 PA 真值表／下一年／结果语义；Green：`allowances.py`＋`bundles/allowances.py`＋两引擎＋bundle 装配＋config | **P1（事实／引擎／API 基础）** | test-writer＋executor-A | §7 p1 |
| M2 | API／AI／记录／脚本接线：validation（v2）、routes（invalidate／If-Match／执行守卫）、consent（原子 CAS）、tools（信封/最小返回）、records、coverage、scripts | **P1（API/契约部分）＋P2（AI/UI 结构部分）** | test-writer＋executor-B | §7 p1/p2 |
| M3 | 引导／发布／更新：真空库判定、五入口守卫、六旧路径 stop-auto 观察、classifier 阻断、reference testgate／publisher 绑定 | **P3（发布路基）** | test-writer＋executor-C | §7 p3a |
| M4 | UI：渐进披露、失效提示、null 呈现、聊天提案采用链（依 designer 004＋Annex 005 §17；隔离顺序按 006 §6：先唯一 DB/假网络 env→导入 main→替换 `_start_scheduler_threads`→构造/启动被注入 app） | **P2（UI）** | frontend-executor＋test-writer | §7 p2/p3b |
| M5 | 完整套件＋真实浏览器＋diff/untracked 审阅＋文档同步＋独立 acceptor | **P3（集成／验收）** | acceptor 只读 | §7 p3c |

- 阶段映射仅用于审批/验证编排：**P1（事实、引擎、API）→ P2（UI、AI、记录）→ P3（完整集成与验收）**；M1–M5 编号与内容不重排；不因中间阶段重启/发布服务（“no shipping/service intermediate”）；PRD 路线图阶段 2（常见扣除）／阶段 3（PA 全集、亏损、跨税项缴款）保持后续原顺序，不因本稿重排。
- **依赖契约（不重排）**：P1 产出 core/store/unit/guard＋**隔离固定 v2 seed 输入**（测试前置，非生产 bootstrap）；P2 仅以假件/注入（假 updater/model、唯一临时 DB）使用该 seed，不依赖真实 updater、不依赖 P3 外部发布；P3 才做生产 bootstrap（`HKTAX_DB_PATH` 真空库）与 publisher 发布链；无中间发布/重启/提交。
- **启动注入（P1/P2，已定依赖契约）**：默认模块级 `app=create_app()` 在隔离测试 store 的构造**不得**要求尚未创建的 P3 生产 seed 证明，也不得由任意 AppError 触发 reseed；有效 app 在获批 seed 前可保持 new-calc blocked（不是错误态）。显式测试注入在 startup 前经既有 seam 构造固定 `TestSeedProvider`；这是高层测试注入，**不新增 CLI/HTTP provider/不安全 env test mode/生产旁路**；观测导入期 main 构造在替换 seam 前不得触真实 store/网络/线程（006 §6 顺序不变）。
- Oracle 闸按阶段：**P1 后**（事实/谓词/引擎/API 语义与独立期望）＋**P2 后**（UI 协议/AI 信封/记录/失效竞态）＋**P3 后**（发布绑定与兼容、完整集成）——**P3 oracle 闸与只读 acceptor 终验为并列必需，不得以 acceptor 替代 oracle 闸（反之亦然）**；@oracle 不替代 @plan-reviewer。
- 里程碑内 TDD：每测试先确认因预期原因失败，executor 最小实现；不得弱化/跳过/删除测试。

## 11. 参考资料与第三方代码使用边界

- 法例与官方行为：Annex D（D6.2a／D7 取整分支；D7.0 s.10/s.29；D10 门禁层级）；已核 Part 7／s.31A／附表 4 原文锚点与协调者逐字更正在证据档 §3–§4 与 `.slim/deepwork/tax-correctness-stage1/legal-followup.md`（official rendered snapshot，非 raw HTTP）。
- 技术契约：Annex C（C2 金额/哈希/上限、C3 状态与确认、C5 安全、C7 参数槽位、C8 门禁）。
- 验证附件：006（历史断言完整处置、报告适配、浏览器隔离、命令）。
- 第三方：不新增依赖；继续不安装 HTML/PDF 解析依赖；参考项目许可记录沿用 001 REQ-16。

## 12. 待核验与未决事项（集成阻断清单）

1. 【已闭】Part 7 原文、s.31A(4) entitled、附表 4 三年度金额/独立 cap：见证据档 §3–§4。
2. 【未核 provenance，非阻断】Ordinance No. 2 of 2026 精确生效文书：s.31(1B) 2026/27 金额/cap 以附表 4 第二表年标为直接依据；精确生效日后续补档，不一概阻断。
3. 【manual 边界，不整类禁算】死亡年度、争议/Commissioner 分配、复杂来源、未核亲属组合、六个月月末边界：经 `special_situation`/`manual_issues` 提交并定位；不猜。
4. 【G2 已批准（2026-10-14；仅 stage1；2026-10-20 已实现（stage1））】公开 v2 事实模型（subject 注册表＋逐年事实＋actual/hypothetical salary-joint 提名字段）；**B1 条件式联合两遍接纳流程与归属矩阵**；有限混合 `pa_self`＋spouse no_pa（含跨模式共享单位 unsupported/unresolved 边界）；候选择案规则；`same_income`/`same_mpf` 投射白名单；PA 恒 partial 状态增量；固定 profile/新空库发布；**协议 delta**：invalidate＋全 7 prepare reservation/If-Match、Operation ACK 头、413 先推进后失败、clear epoch+1/rev0、原子消费快照/完成（外部调用方破坏性变更显式呈报）；**observer 受控 Resolution 边界与报告 purpose seed/update/CAS**；不得静默扩阶段 2/3。**第五稿新增 G2 候选项**：条件 assessment（inactive 字段可省、不写回默认）；availability 显式调用闭包与 replay 原束失败优先级；聊天本地覆盖指引消费者（同源、不动 payload）。
5. 【G2 授权范围】Gate 2 授权 Red cases/fixtures；**新增 auto slot 不因此放行**（六旧路径本期全部 stop-auto）。
6. 【关】已按复审2/闸意见撤回任意资源上限与“输入必须已 canonical”限制；C2 兼容性（尾零/`2/6`）并入 Annex 005 §3。

## 13. 门禁与状态

- 本文档 `Implemented`（stage1；2026-10-20 P3 final5 oracle PASS＋acceptor ACCEPTED；用户 Gate 2 2026-10-14「批准第五稿，进入实施」，范围仅 stage1；历史 formalreview4/r5、council READY 与 P0 attempt3/3 GATE READY 为设计门禁证据）；**历史 OPEN 边界保留；验收不构成完整历史证明**；P1–P3 实施按门禁执行并已关闭。
- 文档 lint（本阶段实际执行、非生产 test）：示例 JSON 解析、profile digest 计算（未变则原值）、内部链接与类型一致性、表格未转义 `|` 完整性、尾随空白/NUL、`git diff --check`/status（含未跟踪）；执行记录见 `sdd-revision-r5.md`。
- 实施门禁：P1／P2 后 oracle 闸＋**P3 后 oracle 闸（强制，与只读 acceptor 并列，不得互替）**＋P3 完整套件＋acceptor；第五稿 fresh affected formal APPROVED、有界 council READY、最后 P0 GATE READY 与**用户 Gate 2（2026-10-14）均已取得**；此后影响规范语义的变更仍须按流程重新审查；未运行／受阻记录为未通过。
- P3-W2 实施记录（2026-10-19）：根全量 pytest（含真实 Chromium e2e）、acceptance 16/16、脚本 001/002 追溯与离线固定束报告、文档／pyproject `0.2.0` 同步已执行；证据见 W2 EV。**本记录为 2026-10-19 历史；当时不改变 Approved 语义，也不标记 Implemented；P3 oracle＋只读 acceptor 终验于 2026-10-20 final5 双终验关闭。**

## 14. 变更记录

- 2026-10-14：建立 002 SDD Draft（第一稿）。
- 2026-10-14（第二稿）：按首轮 17 项完成有界文档修订；后经复审2 与 P0 闸 attempt1 判定 NEEDS REVISION（12＋12 项）。
- 2026-10-14（第三稿）：按复审2 12 项与闸 12 项重构——事实模型改为 registry＋逐年 Year＋独立关系事实（§3；Annex 005 §2–§5）；删除 DeductionIntent/kID、唯一扣除入口＝`financials[p].deduction_items`（§3；Annex 005 §6.2）；逐条款 ANY/ALL 谓词、单亲排除更正、子女无居港、月末/亲属/死亡窄 manual（§2 ADR-7；Annex 005 §7）；候选 actors/units 表与混合 `pa_self` G2 提案、unknown 竞争者阻断 best（§5.4；Annex 005 §9）；PA 恒 partial＋salary partial balance null＋blocked 六键 null（ADR-10；Annex 005 §10）；下一年仅 ID/birth_date 稳定复用＋两种假设＋AvailabilityContext（§5.5；Annex 005 §11）；invalidate＋If-Match＋原子消费与竞态保证（ADR-4；§4.2；Annex 005 §12）；固定 P/digest＋完整矩阵＋六旧路径 stop-auto＋8 条不受影响路径＋报告绑定（ADR-11；Annex 005 §15）；AI 完整信封与最小本地返回（§4.4；Annex 005 §16）；撤回任意上限、C2 词法兼容（ADR-12）；历史断言矩阵去省略并新增 006 完整清单（§9.5；006 §4）；里程碑映射 P1→P2→P3、oracle 按阶段（§10）。状态维持 Draft，Gate 2 未过。
- 2026-10-14（第四稿）：按 review3 七项、P0-r2 B1–B6、corrective-design、supplementary-design 与 ui-singleflight 有界整合——B1 两遍条件式联合/归属矩阵/新提名字段/candidate_checks（§5.3；Annex 005 §4.10/§9.1）；B2 已核普通婚姻窗口替代 ANY 硬否决（§5.3；Annex 005 §9.1 S6）；B3 每候选有效选择/单位覆盖与 shared-unit unresolved（§5.4；Annex 005 §9.3）；R3#4 home_status TF/care 域、item_type 兼容、hk_presence 并集（Annex 005 §3/§4.8/§6.2/§4.7）；R3#5 与 supplement 的 reservation/ACK/413/clear epoch 与 ExecutionContext/Receipt/原子消费完成（§4.2；Annex 005 §12）；R3#6 observer sticky/checking/Resolution 所有权（Annex 005 §15.5）；B5 八路径精准触发/候选收据/seed purpose+empty-CAS/实测门禁（006 §7）；V006 全节点补录与 PA partial steps（006 §4.2、§5–§6）；P 标识随语义同步（Annex 005 §15.1）。状态维持 Draft，Gate 2 未过。
- 2026-10-14（第五稿候选）：按 council 四项合同＋`bounded-closure-design.md` 机械修订——A：F §2.1–§2.2 availability 显式签名/可信来源表/replay 原束失败优先级，S §4.2 引用同步，V §4.5 reference/report 逐调用闭包；B：F §4.10 条件 assessment＋依赖表/8 短路规则/稀疏 PA 映射＋两个计划合成 fixture（§5.3/§5.4），S/V 同步；C：F §4.2 role 历史配偶优先级＋S7 locator/reason；D：F §17 聊天本地覆盖指引消费者（`chat_coverage_guidance(items)`，同源、不动 payload），S REQ-1/V 具名 Red 同步；维护同步：P1 命令补 consume/assessment 节点、S §13 补 mandatory P3 oracle、V B1 文件统一、README/头部同步（review4 历史、council NEEDS REVISION、P0 attempt3 未启动、Gate2 未过）。状态维持 Draft。
- 2026-10-14（Gate 2）：用户明确批准「批准第五稿，进入实施」；SDD/Annex 005/006/004 状态改为 Approved（仅 stage1 P1→P2→P3 严格 TDD；阶段 2/3 另行审批），**未实现（NOT Implemented）**；批准事件不构成设计/测试/验收或运行证据。
- 2026-10-20（Implemented 状态更新）：P3 final5 oracle PASS＋acceptor ACCEPTED；SDD/Annex 005/006/004 状态由 Approved 更新为 Implemented（stage1）；仅文档状态/日期/关闭记录；历史 OPEN 边界保留（45-red、W1b 原始写前字节、F3 全量、pre-seal、21 项 history_unavailable、视觉 B/D/F、stage2/3 未扩）；代码/测试/文档语义未变。
