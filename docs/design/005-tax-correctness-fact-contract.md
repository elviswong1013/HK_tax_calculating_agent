# 005 — 税负正确性阶段一：公共事实契约与封闭字段表（Annex 005，Approved 第五稿）

Status: Approved
Created: 2026-10-14
Updated: 2026-10-14（Approved 第五稿：按 council NEEDS REVISION 四项合同＋bounded-closure-design 补 §1.4 provenance、条件 assessment 完整依赖表、S7 历史配偶、聊天覆盖来源；修订对照见 `.slim/deepwork/tax-correctness-stage1/sdd-revision-r5.md`）；用户 Gate 2 于 2026-10-14 明确批准「批准第五稿，进入实施」；状态 Approved（仅 stage1；NOT Implemented）
关联: docs/prd/002-tax-correctness-remediation.md（Approved，Gate 1 2026-10-14）；specs/002-tax-correctness-remediation.md（Approved 第五稿，本附件为其 §3/§4/§5 的完整 schema 附件）；docs/design/006-tax-correctness-verification.md（Approved 第五稿，验证附件）；docs/design/002-hk-tax-technical-contract.md（Annex C，Approved，技术契约权威）；docs/design/003-hk-tax-legal-contract.md（Annex D，Approved，税法语义权威）；docs/design/004-tax-correctness-ui.md（Approved，UI delta，视觉意图由 designer 保留）

> 状态声明：Approved 附件（第五稿）；formalreview4 APPROVED 与原 council NEEDS REVISION 保留为历史；**受影响正式审查 APPROVED（review-plan-r5）＋有界 council READY（council-closure-r5）＋最后 P0 attempt3/3 GATE READY（gate-p0-r3）已归并为设计门禁；用户 Gate 2 于 2026-10-14 明确批准（「批准第五稿，进入实施」）**——范围仅 stage1 P1→P2→P3 严格 TDD（阶段 2/3 另行审批）；**未实现（NOT Implemented）**；批准事件不构成设计/测试/验收或运行证据。本稿为有界文档修订：采用协调者已通报的冻结就绪契约方向（`fact-design.md`＋`technical-design.md`＋corrective/supplementary＋`bounded-closure-design.md` 及其协调者 reconciliation），不新造架构；凡与前稿冲突处**整体替换**，不只补声明。所有字段为**封闭键集合**：请求中出现未列字段一律按 C3.7 `E_INPUT_EXTRA` 拒绝（手写封闭字段表实现，沿用 `app/api/validation.py` 现行 `_REQUIRED/_ALLOWED`＋AppError 风格；不得假定 Pydantic 自动补齐或自动裁剪）。金额与哈希语义以 Annex C 为权威，税法语义以 Annex D 与证据档为权威；本文只冻结公共字段、口径、谓词、方案与状态契约，不创造法条。示例均为合成资料，无姓名/HKID/地址/医疗诊断。

## 1. 范围与效力

- 适用税项：薪俸税（`salaries_tax`）与个人入息课税（`personal_assessment`）input schema `2.0.0`；其余五税项 input 维持 `1.0.0`，本文不适用。
- 本文取代第二稿的 `facts_by_year`／`marital_status_by_year`／`pa_age_condition_met`／`pa_residence_condition_met`／`Year.deductions`／`DeductionIntent(kID)` 等已废止表述；**新计算只接受本稿**，旧 1.0.0 输入明确拒绝（不迁移，见 SDD §4.2）。
- 未经 Gate 2 批准，本文任何表格都不是实现授权，也不得晋升为 fixture 或规则数据。

## 2. 请求信封、键年、稳定复用与 AvailabilityContext

- 信封不变：`{"tax_type","schema_version":"2.0.0","input"}`；`extra="forbid"`。
- **键年规则**：`years` 只允许**目标 YA 键＋其紧邻下一年键**（请求 `2025_26` → `2025_26`、`2026_27`）；其余年键 → `E_INPUT_EXTRA`；上一年键不受理。下一年键缺失＝该年组件未知（≠不申索、≠零），只影响下一年结果。
- **稳定跨年复用仅限**：`subject_id` 与已知 `birth_date`。`role`、`claims`、`assessment` 选择、`assumptions` 之外的一切资格事实**逐年在 Year 内重新提交**；不得把本年 role／申索／选择自动带入下一年。
- **AvailabilityContext（显式参数，进 HTTP 预检、AI 预检与 v2 纯引擎）**：
  ```text
  AvailabilityContext = {
    binding: {bundle_id,bundle_hash,rules_schema_version,engine_version},
    current: {year, status:"available"|"blocked"|"missing_rules", reason_codes:[string]},
    next:    {year, status:"available"|"blocked"|"missing_rules", reason_codes:[string]}
  }
  ```
  规则：缺 context **不等于**允许（无默认放行）；纯引擎不读 current store、不联网；HTTP/AI 预检与执行取得同一 guard 快照，并在消费临界区复核仍有效；blocked 仅抑制受影响年度（本年 blocked → 不产确定税额；next blocked/missing_rules → 仅 next，本年 final 原样保留）；任何缺失一律**不回退本年度规则、不补默认**。

### 2.1 引擎签名与 AvailabilityContext 可信来源（A 合同）

```text
run_engine(tax_type, facts, bundle, *, availability=None) -> result
calculate_salaries_tax(facts, bundle, *, availability=None) -> result
compare_personal_assessment(facts, bundle, *, availability=None) -> result
build_public_availability(binding, bundle, year, *, guard_snapshot) -> AvailabilityContext
build_case_availability(case_descriptor, bundle, *, purpose) -> AvailabilityContext
recompute_for_replay(core, store, engine_version, *, availability_provider) -> result
```

- `None` 仅保留分派签名兼容，**不是允许默认**：薪俸/PA 缺 context 或不匹配即 `E_RULES_STATE_UNVERIFIABLE`；其余五税签名/算法不变。
- context 必须与**实际传入 bundle** 的 hash/schema、执行 engine 版本、目标＋紧邻下一年一致；不得只检查字段存在。context 只由下表可信调用方构造；HTTP/AI 业务信封禁止提交；不是客户端权限 flag，不新增 input/core/binding 字段。
- 现有 `_consult_availability` 的“guard 缺席/期不可得→跳过”（`routes.py:750–782`）不得用于新两税路径。

| 路径 | bundle 及 context 来源 | 必须迁移的实际入口 |
| --- | --- | --- |
| HTTP prepare | pin 当前完整 bundle；读取当前及下一年度可信 guard 快照；查该 bundle 规则完整性 | `routes.py:100–106,704–782` |
| HTTP execute | 原子 consume 的完整 ExecutionContext；消费后不再读 prepared/current | `routes.py:257–275`→`records.run_engine` |
| AI propose/validate | 与 HTTP 相同已 pin bundle 及公开 guard 来源；无能力创建 | `ai/tools.py::ToolContext`＋两只读工具 |
| AI calculate | 与 HTTP 共用 consume 快照，availability 传同一 dispatch；废除消费后重取 facts | `ai/tools.py:329–338` |
| compatible replay | **record 原 binding、原 bundle**＋当前可信 guard 对相关年度准入 | `routes.py:310–332`→`records.py:537–562` |
| static regression | 固定完整 v2 baseline＋获批 case descriptor 规则/期间证据 | `reference.py::_run_salaries_q1`／`::_run_pa_q32`／`CandidateEngine.run_case` static 分支 |
| affected candidate | 完整原样 candidate＋独立 proof-controlled descriptor；仅隔离数值验证准入 | 同上 candidate 分支；**不得重置 data 区** |
| 离线/对比报告 | 明确绑定固定 bundle 及 descriptor；注明“固定束核验/模拟，不代表实时规则可用性” | §2.2/V §4.5 所列报告直接调用 |

- `ReferenceHarness.run_case`（`reference.py:199–215`）只提供固定独立阶段/期望，不执行生产引擎、不产“对外 available”裁定。`CandidateEngine`（`:235–240`）现单 domain 回填只允许替换成**完整 static baseline 路径**；affected candidate 路径不允许任何回填。
- `build_case_availability` 仅 case/report 受信入口：校验 descriptor 的 bundle/profile/hash、允许年度、独立规则证据及 case 范围；**候选身份或 JSON 齐全≠获准**。descriptor 的 context policy 纳入既有 `input_proof_sha256` 绑定内容；不得进入 HTTP/AI 公开准入。固定 baseline 的规则来源可先于测报存在，不要求“先通过本 case 才能构造本 case context”的循环。

### 2.2 Replay 原束绑定与失败优先级（七目标统一）

> 先验证 engine 完全相等，再装入原 bundle 并验证原 hash/schema 及本引擎固定 profile；成功后以原 input 目标年度构造 context；guard 只决定该年度是否准许本次重算、不提供替代数值；`context.binding` 始终为 record 原四元组，绝不调用 `pin_current`。

| 情况 | 处理 |
| --- | --- |
| engine 不等、原 bundle 不存在/hash 或 schema 不符、不支持其 profile | 409 `E_BUNDLE_INCOMPATIBLE`（先于 context 查询；七目标统一） |
| 新请求期间缺失/非法/不支持 | 既有 `E_INPUT_*`／`E_PERIOD_NOT_SUPPORTED`；不以当前年补齐 |
| 原束兼容，但公开 guard 缺失、当前年畸形/unknown/blocked | 422 `E_RULES_STATE_UNVERIFIABLE`；不默认 available |
| 原束当前年规则缺项 | 同上；规则缺失不是法定不适用 |
| 当前年可用，仅下一年缺/拒绝/不可核验 | 保留当前确定金额；next=null、partial、salary balance=null |
| 当前/next 可信 ok 或 trusted_offline，且原束规则完整 | 可构造 available；offline 提示保留，不宣称使用最新规则 |
| 原束缺下一年，而 latest 有 | 仍 missing_rules；不得借用 latest |

- guard 是年度准入裁定，**不要求原 bundle 等于当前指针**；“当前指针已换、原束仍兼容且 guard 允许”的成功 replay 必须保留；原 bundle ≠ current pointer 本身不拒绝。
- replay 响应继续标历史重算；**不保存新 record、不修改原 core/hash/input/steps、不产生或改变 revision 与确认能力**；仅 next 被当前 guard 抑制时，不宣称返回分项与保存 core 逐字相同；原记录查看/打印/下载仍原字节。兼容重算不经 HTTP prepare，不改变 editor revision。
- **范围区分**：本表所列 engine/hash/schema/profile 兼容检查为**七目标共同**的严格检查；“current/next 年度 context”规则仅适用于薪俸/PA 两个新引擎。**其余五税保持既有 current-period C7 guard、无 next 概念与既有算法**（§12.3），仅随共享签名/协议适配，不得扩成 year-next 逻辑。

### 2.3 Reference/report 逐 case provenance（A 合同；非 wire）

```text
AdaptedCase = {envelope,
  field_provenance:[{field, origin:"source"|"synthetic_control", evidence_ref}],
  raw_case_hash, controls_hash, context_policy}
```

- 采用逐 case 适配器＋非 wire sidecar，不提供通用 v1→v2 迁移。原官方 source bytes、原数字事实及独立期望阶段/tier 不变；**新语义字段必须有实际来源或显式获批 `synthetic_control`**；不得由 legacy builder 的零值/日期/none/ordinary/居民推断 year TF、AI=false 等。
- Q1：保留 240000、9000、2025_26 与 3940/3000/940；全年未婚、source 分类、空申索等若原件未明确，必须列为独立、显式 T3 控制条件。Q32：保留 250000 薪俸、配偶 240000 租金与既有组件（12920/3000/9920、no_pa 28800）；婚姻窗口、AI/全来源存在性、PA 年龄/居港、参考日、实际选择/history 均不得从旧 builder 默认推得。PAM37/随机批：原数值/比例与来源等级不变；随机批明确模拟、不证明现实资格。
- 缺活动路径所需事实且无获批 control → case 记**不可计算**；必需 gate case 则 **mandatory quarantine**，不得删 case、改独立 expected 或旧官方 bytes 求绿。controls 走**既有独立 Red/fixture 审阅流程**，不新增用户门、发布能力或 runtime case。
- next 缺事实/授权只作不可估算；不为维持旧 complete/next 数值复制婚姻、选择或资格。`_single/_person/_pa_facts` 不得成为默认补资格器；两税改用逐 case 适配。

## 3. 类型定义与封闭根形状

| 类型 | 精确定义 |
| --- | --- |
| `Tri` | JSON 原始值 `true / false / "unknown"`；v2 全文统一该原始值，**不再混用 `{value:…}`**（唯一例外：§6 既有扣除项的 legacy `eligibility` 包装） |
| `Date` | wire 必须 `YYYY-MM-DD` 全匹配（C2.11；不接受时间戳/紧凑日期），再做日历校验 |
| `YA` | `YYYY_YY`；计算目标仅 `2024_25`、`2025_26`、`2026_27`；`years` 另允许紧邻下一年键 |
| `Money` | 输入与组件金额（financials／rules／deductions／care_payments 等）为非负 canonical 定点字符串；**唯一有符号例外：core `amounts.balance` 可为负**（C2 已批有符号 AmountStr；负号仅该余额/退税语义，PA balance 仍恒 null；不得据此放开 NAI/care/法定 caps 等输入为负）。**输入词法完全沿用 C2.1/C2.10 AmountStr**：ASCII 全匹配、长度 ≤32、`\|值\| ≤ 1e14`、小数 ≤2 位；`"1200000.00"` 必须接受并规范为 `"1200000"`；拒绝 JSON number/null/boolean/指数/空白/千位符/前导零；缺省 ≠ `"0"` |
| `Ratio` | wire 字符串 `num/den`（整数）。输入允许未约简（如 `"2/6"`）并在规范化时约简为 `"1/3"`；`0<num≤den`；禁止 number/null；与 Money 同类词法处理，不引入“必须已 canonical”的新拒绝 |
| `ID` | 前缀＋1–31 个小写字母/数字/连字符：人=`p`、其他 subject（dependant）=`d`、关系=`r`、claim=`c`、care payment=`e`、物业=`h`、业务=`b`、假设=`a`。**无 `k`（DeductionIntent 已删除）** |
| `TF` | 时间事实＝`[{from:Date,to:Date,value:Tri}]`，**左闭右开**、`from<to`、按序且互不重叠；空数组及未覆盖日期＝未知。默认覆盖窗口＝该 YA 的 `[4月1日, 次年4月1日)`；仅 §7 列明谓词可额外覆盖参考日/选择日的一个“一天区间”；**例外：`care_payments[].home_status_in_force` 的合法域＝其自身 `[care_from,care_to)`，可完整落在付款 YA 之前（见 §4.8），不受默认 Year 窗口截断，也不因此开放其他任意年外 TF**。**不设 TF32 项等新增上限**（撤回，见下） |
| `MarriageTF` | 同 TF 区间形状，value=`"unmarried" / "married" / "unknown"`；`married` 区间必须带 `spouse_id:pID`，其他区间禁止该键 |

```text
Envelope = {tax_type:"salaries_tax"|"personal_assessment", schema_version:"2.0.0", input}
Input    = {year_of_assessment:YA, subjects:[Subject], years:{YA:Year},
            hk_presence_days?:[PresenceDays], assumptions?:[Assumption]}
Subject  = {subject_id:pID|dID, birth_date?:Date}
Year     = {participants:[Participant], subject_facts:{subjectID:SubjectFacts},
            household:Household, relations:[Relation], financials:{pID:Finance},
            care_payments:[CarePayment], claims:[Claim], assessment:Assessment,
            manual_issues?:[ManualIssue], provisional_paid?:Money}
Participant = {person_id:pID, role:"self"|"spouse"}
```

- **域约束（非通用预算）**：每年（含下一年键）恰 1 名 `role="self"`、最多 1 名 `spouse`；`self` 的 pID 跨年稳定；`spouse` 可逐年新增/退出，role 与婚姻**不跨年继承**。注册但未在某年参与的人员不自动计入该年税额。
- **资源限制（撤回新增任意上限）**：仅沿用 C2.9 既有上限（请求体 ≤1MB、全局数组 ≤1000 项、合格子女申索 ≤9）及既有明确限制；**撤回**第二稿/提案中的“日期 1900–2100”“TF ≤32”“每人 32 物业”“3 个 pID/64 个 dID/128 claims”等任意新增 cap。`TF` 数组、子女出生日期→名次仍按 C2.9/既定规则处理（名次由服务器派生，不是权威输入）。
- 每年使用的全部 subject/person 引用必须已在 `subjects` 注册；`pID` 才能作为 claimant/payer/participant。ID 去重只按显式 ID：同 ID 重复条目 → `E_INPUT_CONTRADICTORY`；**不以生日识人**（同生日不同 ID 的双胞胎合法）。
- **可选≠可空**：`Input` 中 `hk_presence_days?`／`assumptions?` 的 `?` 仅表示**键可省略**（省略保持省略），**不表示可空**；两者显式 `null` → `E_INPUT_TYPE`。（既有已批解释的记录，非新政策；不泛化其他可选键的既有缺失语义。）

## 4. 完整封闭字段表（字段/类型/必填/说明）

### 4.1 `subjects[]`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `subject_id` | `^p…\|^d…` | 是 | 会话内稳定标识；不收 HKID/姓名 |
| `birth_date` | `Date` | 触发路径时必填 | 子女/兄弟姊妹年龄路径、父母/祖父母年龄档；仅注册人员可省略 |

### 4.2 `Year.participants[]`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `person_id` | `^p…` | 是 | 必须已注册；`self` 跨年不变 |
| `role` | `"self"\|"spouse"` | 是 | 每年恰 1 self、≤1 spouse；年度 spouse participant 表示本请求的年度计算 actor，**不声明其为该 self 在整年唯一历史配偶**；`MarriageTF` 可顺序引用已注册但不参与计算的历史 pID。合法非重叠顺序中的不同 `spouse_id` **不单凭不同于年度 actor 判矛盾**；结构校验通过后，依赖该婚姻变化的路径进入 S7。历史 pID 不得自动成为第三个 actor。结构优先级：① JSON/类型/日期格式错误＝既有输入错误；② 悬空 ID、非 pID、自身配偶、重复 subject/participant、>1 年度 spouse、区间重叠 → `E_INPUT_CONTRADICTORY`；③ 相关窗口全部已明确且声明 actor 完全无对应婚姻、存在已证反证 → 仍矛盾（**仅未知/未覆盖不是反证**）；④ 已注册、排序、非重叠的顺序配偶变化 → S7（非全历史一致性拒绝）；⑤ 相关 joint/s.29/PA/归属路径 manual/unresolved 并生成既有 locator；⑥ 只有 truly independent separate 才可保留，禁止把未参与历史配偶收入当零或把当前两名 actor 合计冒称家庭总额 |

### 4.3 `Year.subject_facts`（对象映射：`{subjectID: SubjectFacts}`；全部字段可省略，省略＝未知）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `unmarried` | `TF` | 触发路径时必填 | 子女/兄弟姊妹未婚期间；participant 的婚姻事实由 `household` 派生，**禁止**对 participant 重复提交 |
| `govt_disability_allowance` | `TF` | 触发路径时必填 | 政府伤残津贴**资格**期间（不问领取） |
| `full_time_education` | `TF` | 触发路径时必填 | 18–25 全日制教育期间（以 TF 表达，不再有独立 `education_period` 对象） |
| `incapacitated_for_work` | `TF` | 触发路径时必填 | 18+ 因伤残不能工作期间 |
| `ordinarily_resident_hk` | `TF` | 触发路径时必填 | 通常居港；可额外覆盖 assessment 参考日（§7） |
| `both_parents_dead` | `TF` | 触发路径时必填 | 双亲已亡（PA 年龄肢替代路径）；可额外覆盖参考日 |
| `death_date` | `Date` | 否 | 死亡事实；死亡年度影响总额时转 manual（§8.4），不整类禁算 |

### 4.4 `Year.household`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `marriage` | `MarriageTF` | 是 | 年度婚姻期间；married 必带 `spouse_id`；可额外覆盖参考日/实际选择日 |
| `living_apart` | `TF` | 是 | 分居期间；**仅在有婚姻/参考日的范围内求值**；未婚期间不要求填写、也不据空白推断 false |
| `child_nomination` | `{person_id:pID\|null, status:"none"\|"agreed"\|"disputed"\|"unknown"}` | 是 | s.31(3) 非分居夫妻子女**整组**提名；`disputed`/`unknown` → manual_handoff；`none` 与 `unknown` 区分保留；适用整组时不得逐子女自由拆分 |

### 4.5 `Year.relations[]`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `relation_id` | `^r…` | 是 | 唯一；同 ID 重复 → `E_INPUT_CONTRADICTORY` |
| `actor_id` | `^p…` | 是 | 申索人/供养人 |
| `subject_id` | `^p…\|^d…` | 是 | 被申索亲属；每年 `(actor_id,subject_id)` 唯一 |
| `kinship` | `Kinship?` | 否 | 见下；仅当需要关系定义时提交 |
| `maintained` | `TF` | 触发路径时必填 | 子女/兄弟姊妹受供养期间 |
| `cash_maintenance` | `Money` | 否 | **该 actor 自己**本年支付的供养总额（不重复填写夫妻合计） |
| `cohabit_non_full_consideration` | `TF` | 否 | 父母/祖父母非足值对价同住期间（连续 ≥6 个日历月判定用） |
| `sole_or_predominant_care` | `TF` | 触发路径时必填 | 主要/独力照顾期间（兄弟姊妹、单亲） |

- `Kinship = {kind:"child"|"parent"|"grandparent"|"sibling"|"none"|"unknown", basis:"birth"|"adoption"|"step"|"full_blood"|"half_blood"|"child_of_adoptive_parent"|"other"|"unknown", during:TF}`。
- kinship 表达 subject 与 actor 的**直接关系**；配偶的亲属由服务器经当年婚姻关系连接，不另存 spouse-parent 资格副本。
- 普通已核映射（冻结）：child 的 birth/adoption/step；parent 的 birth/adoption；grandparent 的 birth；sibling 的 full_blood/half_blood/adoption/step/child_of_adoptive_parent。其余组合**可提交**，但在该关系定义未核清前定位 manual（**窄边界，不整类禁算**）。

### 4.6 `Year.financials`（对象映射：`{pID: Finance}`；仅当年 participant）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `employment_income_nai` | `Money` | 薪俸/PA 计税路径必填 | s.12 调整后、Part 4A 前的净应评税入息（NAI）；缺省 ≠ `"0"` |
| `mpf_mandatory_contributions` | `Money` | 薪俸/PA 计税路径必填 | 缺省 ≠ `"0"`；封顶由引擎按附表 3B 计算 |
| `has_assessable_salary_income` | `Tri` | 夫妻法定判定路径必填 | ss.11B–D 薪俸 AI **存在性**；正 NAI 与此值 false 并提 → `E_INPUT_CONTRADICTORY`；NAI=0/缺失不得推断此值 |
| `has_assessable_property_income` / `has_assessable_business_income` | `Tri` | 来源存在性判定时必填 | 与已确认应评税物业/业务组件交叉约束（§6.1） |
| `has_income_assessable_under_ordinance` | `Tri` | 仅 PA joint 候选必填 | s.41(1A) 逐字全来源测试；不得以薪俸 AI/NAI 替代 |
| `source_classification` | `{salary?:SourceClass, property?:SourceClass, business?:SourceClass}` | 否 | `SourceClass = "confirmed_in_scope"\|"unknown"\|"manual"`；是用户已确认的来源分类事实，不是服务器自动来源裁定；unknown/manual 影响哪个候选就阻止该候选成为确定金额/best |
| `properties` | `Property[]` | 否 | 见下；`[]`＝明确没有列入该类组件，省略＝未知 |
| `businesses` | `Business[]` | 否 | 见下 |
| `deduction_items` | `DeductionItem[]` | 否 | **唯一的扣除意图入口**（`Year.deductions`/`DeductionIntent`/`kID` 已删除）；见 §6.2 |

- `Property = {property_id:hID, share:Ratio, rent_received:Money, rates_paid_by_owner:{amount:Money, agreed:Tri, actually_paid:Tri}, irrecoverable_rent:Money, deposit_offsets:Money, mortgage_interest:Money}`；同一 hID 的共同原始字段必须一致，份额/个人利息可不同；公式沿用既有已批语义。
- `Business = {business_id:bID, entity_kind:"sole_proprietorship"|"partnership", assessable_profit:Money, share:Ratio}`；公式沿用既有已批语义。
- 金额/数组**逐年重新提交**；下一年不得复用本对象。物业/业务需要尚未支持的外部共有人分配、亏损/转移等路径时，该候选明确 manual/unsupported，不新增算法。

### 4.7 `hk_presence_days?`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `person_id` | `^p…` | 是 | 每人/年唯一日数记录 |
| `year` | `YA` | 是 | 允许年份＝对所有**显式请求 Year** 取 `{Y−1,Y,Y+1}` 的并集（用于 s.41 temporary-resident 辅助）；只开放辅助日数，不开放这些年的计税规则或其他事实；**语法合法但不在该并集内的 YA 值 → `E_INPUT_EXTRA`**（`error.field`＝真实输入路径 `hk_presence_days[index].year`，按 §13；**不是** TF 域 TYPE）；不扩张计税年度、不把该并集扩成 TF 域 |
| `days` | 整数 `0..该YA日数` 或 `"unknown"` | 是 | 同一日数可在多个候选共用；PA 居港阈值“严格大于”（§7/§9） |

### 4.8 `Year.care_payments[]`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `payment_id` | `^e…` | 是 | 唯一 |
| `payer_id` | `^p…` | 是 | 本人或非分居配偶 |
| `subject_id` | `^p…\|^d…` | 是 | 对应亲属/长者 |
| `paid_on` | `Date` | 是 | 必须落在键年 |
| `amount` | `Money` | 是 | 实际已付金额；不另设“可扣金额”输入 |
| `care_from` / `care_to` | `Date` | 是 | `care_from<care_to`；住宅照顾期间 |
| `residential_care_received` | `Tri` | 是 | 具体事实，**不是** `eligible_for_s26D` |
| `paid_to_home_or_agent` | `Tri` | 是 | 收款对象事实 |
| `home_basis` | `HomeBasis` | 是 | `"cap459_licence"\|"cap459_s3_excluded"\|"cap633_scheduled_nursing_s128_exemption"\|"cap613_licence"\|"cap613_exemption"\|"other"\|"unknown"` |
| `home_status_in_force` | `TF` | 是 | 合法域＝该付款自身的 `[care_from,care_to)`（左闭右开）；可完整落在付款 YA 之前（上年服务、本年付款）；不被默认 Year 窗口截断，也不得含该 care 区间之外的期间 |

### 4.9 `Year.claims[]`（显式数组；`[]`＝本年不申索任何免税额）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `claim_id` | `^c…` | 是 | 唯一 |
| `kind` | `ClaimKind` | 是 | `child`、`parent`、`grandparent`、`sibling`、`single_parent`、`personal_disability`、`disabled_dependant`、`parent_cohabitation_additional` |
| `claimant_id` | `^p…` | 是 | 必须引用当年 participant |
| `subject_id` | `^p…\|^d…` | 是 | `disabled_dependant.subject` 可为 spouse person |
| `special_situation` | `Tri` | 否 | 普通路径省略；true/unknown → 该 claim 决策 manual_handoff＋locator（claim_id/field/year），**可提交、可定位，不整类禁算** |

- claims 只声明**意图与引用**；一切资格事实独立存在于同年的 `subject_facts`/`household`/`relations`/`financials`/`care_payments`，**不得由 claim 类别反推资格**，也不得由 `kind` 直接授予 entitlement。已提交 claim 缺触发路径必需事实＝needs_input（§13），不得解释为不申索。

### 4.10 `Year.assessment` 与比较请求

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `basis` | `"actual"\|"hypothetical"` | **无条件必填** | actual＝只请求与实际选择一致的候选；hypothetical＝为各候选生成“假设作出该选择”的 trace，实际事实不变 |
| `candidates` | `CandidateID[]` | **无条件必填** | 显式、非空、无重复；见 §9；不隐式增加候选 |
| `selection` | `"best"\|CandidateID` | **无条件必填** | 见 §9；`best` 只有一个含义 |
| `actual`（对象） | 对象 | **按活动消费者条件出现** | 省略保持省略（不写回 none/ordinary/日期）；出现时其成员按封闭键/类型/引用/已证矛盾严格校验；显式 unknown 保留 |
| `reference_date` | `Date` | 条件 | hypothetical 的 `pa_self`/`pa_joint` 需要 P1/P2 时；缺失→该候选 needs_input；**Date 不接受字符串 "unknown"** |
| `actual.pa` | `{pID:EffectivePAEntry}`（稀疏） | 条件 | 键只允许当年 participant、可为稀疏；`EffectivePAEntry={mode:"none"\|"individual"\|"joint"\|"unknown", date?:Date}`；`none` 禁止 date；individual/joint 的 date **仅在日期消费者活动时必填**（已提供则严格校验）；joint 须双方同为 joint 且日期一致（已提供的配对 mode/日期冲突→拒绝；配对信息缺失＝需补事实，非矛盾） |
| `actual.history` | `"ordinary"\|"withdrawn"\|"disputed"\|"unknown"` | 条件 | joint/PA 选择资格、s.29 选择状态或转换确实依赖撤回/争议历史时；缺失/unknown→相关路径未决；**ordinary 不能补足其他未知事实**、不得默认写回 |
| `actual.salary_joint_elected` / `actual.salary_joint_assessed` | `Tri` | 条件 | 前者：ordinary 夫妻实际 salary 模式核对、joint 请求或转换；后者：s.41(1B)、actual no_pa 的 salary 模式核对、已有 joint 评税的冲突/转换 |
| `actual.salary_joint_nomination` / `hypothetical_salary_joint_nomination` | `Nomination?` | 条件（s.10(2)(b) 归属路径） | 维持既有 none/unknown/disputed 规则；与 child nomination 严格不同 |
| `Nomination`（类型） | `{status:"none"\|"named"\|"unknown"\|"disputed", person_id:pID\|null}` | —— | `named` 必须引用当年两名 participant 之一；其他 status 必须 `person_id=null`；需归属而缺字段/unknown → needs_input；none → 要求指定；disputed → manual |

**显式依赖表（有限 7 行；消费者/缺失/首页条件）**：

| 字段 | 活动消费者/触发条件 | 缺失或 unknown | 首页条件 |
| --- | --- | --- | --- |
| `reference_date` | hypothetical 的 `pa_self`/`pa_joint` 需要 P1/P2 时 | 缺失→该候选 needs_input；Date 不接受字符串 `unknown` | 普通 salary 不显示、不要求 |
| `actual.pa[p].date` | actual PA 候选的年龄/居港/选择日期；或确实需日期判断的转换 | 缺日期→相关候选 needs_input | salary 仅在真实消费者要求日期时显示 |
| `actual.pa[p].mode` | s.29 分支 A 确实需要该配偶“未 individual PA”；salary_joint 实际选择兼容；PA 实际模式/转换检查 | 缺失或 unknown 只阻相关候选；不填 `none` | 仅相应夫妻/转换路径显示 |
| `actual.history` | joint/PA 选择资格、相关 s.29 选择状态或转换确实依赖撤回/争议历史 | 缺失/unknown→相关路径未决；`ordinary` 不能补足其他未知 | 删除“所有 salary 必填”；按活动路径显示 |
| `salary_joint_elected` | ordinary 夫妻实际 salary 模式核对、joint 请求或转换 | 缺失/unknown→相关候选未决 | 单身 separate 不显示 |
| `salary_joint_assessed` | s.41(1B)、actual no_pa 的 salary 模式核对、已有 joint 评税的冲突/转换 | 缺失/unknown→相关候选未决 | 单身 separate 不显示 |
| 两种 salary nomination | 已冻结的 s.10(b) 归属路径 | 维持现有 none/unknown/disputed 规则 | 不变，按路径显示 |

**有限依赖与短路规则（8 条）**：

1. 明确全年未婚＋仅 `salary_separate`/basic：不读取上述 inactive 字段；actual/hypothetical 均可；不据此产生“未选 PA”事实。
2. s.29 分支 A 已因配偶 AI=true 明确不成立时，不为该分支追问配偶 PA mode；仍检查其他活动分支。
3. PA 请求只选 `no_pa` 时：不评估 P1、不要求 reference_date；其实际 PA 模式与 salary 模式仍按 no_pa 一致性/转换需要确认。
4. 明确不合资格的候选不再追问不会改变结论的字段；未知竞争者不得因此被当不合资格删除。
5. active actual PA：日期取该 actor 的实际 election date；joint 必须双方一致；reference_date 可省略；若同时提交且不等于所用实际日期 → 报矛盾，不静默择一。
6. hypothetical PA：取 reference_date；实际 PA 日期只用于检查已有选择/转换，不覆盖假设日期。
7. `actual.pa` 稀疏 map：`none` 禁止 date；`individual/joint` 的 date 仅在日期消费者活动时必填；joint 配对信息缺失＝需补事实，已提供的配对 mode/日期互相冲突仍拒绝。
8. 显式提供的已知选择与请求冲突时，仍走既有矛盾/转换规则；“条件必填”不授权忽略反证。

**内部 EffectiveAssessment 勘误（B 合同）**：salary 行的 `pa_by_person` **只复制实际存在的条目**，允许缺键；不为缺键插入 `none`/`unknown` 对象。消费者先判依赖是否活动，再读取该 key；活动且缺 key → needs_input。PA 候选的 none/individual/joint 是已明确候选的派生假设，不是补写 actual，也不隐式加入 no_pa。独立已知、显式选中的 separate 可按既有 partial 协议继续；另一候选未决使 best=null，不升级为无条件清空已知结果。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `manual_issues[]` | `{scope:"person"\|"subject"\|"relation"\|"property"\|"business"\|"election", id:ID, code:"death_in_year"\|"dispute"\|"source_uncertain"\|"loss_or_transfer"\|"other"}` | 否 | **键可省略**（省略保持省略，**不补 `[]`**、不写回默认）；显式 `[]` 合法；显式 `null` → `E_INPUT_TYPE`；其余八个 `Year` 容器保持必填（既有缺键 `E_INPUT_MISSING`、显式 `null` `E_INPUT_TYPE` 语义不变）；人工边界定位 |
| `provisional_paid` | `Money` | 否 | 缺失 ≠ `"0"`；部分结果语义按 C3.4/§10 |
| `assumptions[]` | `Assumption` | 否 | 仅下一年投射假设（§11） |

当 `manual_issues[].scope="election"` 时，`id` 必须是该 Year `participants` 引用的 pID，指向该年度该参与者的评税选择；不得新造 election ID。仅依赖该选择的候选进入 unresolved/manual；不依赖它的已知候选结果保留。可能竞争的 unresolved/manual 候选使 `best=null`，但独立已知的显式 `selected` 仍保留；不得将该 manual 候选当作 ineligible 删除。

## 5. 有效完整请求示例（封闭契约示范，非 fixture、未执行）

### 5.1 薪俸税：只申索伤残子女额、没有基本 claim（出生年部分年度同点资格）

```json
{
  "tax_type": "salaries_tax",
  "schema_version": "2.0.0",
  "input": {
    "year_of_assessment": "2025_26",
    "subjects": [
      {"subject_id": "p1", "birth_date": "1990-01-01"},
      {"subject_id": "d1", "birth_date": "2025-12-01"}
    ],
    "years": {
      "2025_26": {
        "participants": [{"person_id": "p1", "role": "self"}],
        "subject_facts": {
          "d1": {
            "unmarried": [{"from": "2025-12-01", "to": "2026-04-01", "value": true}],
            "govt_disability_allowance": [{"from": "2026-01-01", "to": "2026-04-01", "value": true}]
          }
        },
        "household": {
          "marriage": [{"from": "2025-04-01", "to": "2026-04-01", "value": "unmarried"}],
          "living_apart": [],
          "child_nomination": {"person_id": null, "status": "none"}
        },
        "relations": [
          {"relation_id": "r1", "actor_id": "p1", "subject_id": "d1",
           "kinship": {"kind": "child", "basis": "birth",
                        "during": [{"from": "2025-12-01", "to": "2026-04-01", "value": true}]},
           "maintained": [{"from": "2025-12-01", "to": "2026-04-01", "value": true}]}
        ],
        "financials": {
          "p1": {"employment_income_nai": "240000", "mpf_mandatory_contributions": "9000",
                  "has_assessable_salary_income": true,
                  "source_classification": {"salary": "confirmed_in_scope"},
                  "properties": [], "businesses": [], "deduction_items": []}
        },
        "care_payments": [],
        "claims": [
          {"claim_id": "c1", "kind": "disabled_dependant", "claimant_id": "p1", "subject_id": "d1"}
        ],
        "assessment": {
          "reference_date": "2026-06-01",
          "basis": "actual",
          "actual": {"salary_joint_elected": false, "salary_joint_assessed": false,
                      "pa": {"p1": {"mode": "none"}}, "history": "ordinary"},
          "candidates": ["salary_separate"],
          "selection": "salary_separate"
        },
        "manual_issues": [],
        "provisional_paid": "0"
      }
    },
    "hk_presence_days": [],
    "assumptions": []
  }
}
```

- 本例子证明：出生后部分年度的未婚/供养/DAA 事实可表达；三年龄路径为替代路径，教育/不能工作无需填写；关系事实独立于 claim；`disabled_dependant` 依赖 child **entitlement** 成立而**不要求先有基本 child claim**，未申索的基本额/出生额外额不自动计入；单候选分别评税为默认，`basis="actual"`。**示例中的 `reference_date`/`actual` 为 OPTIONAL 显式提交的合成事实（非必需、不写默认）；inactive 字段可省略（§4.10），最小可省形式见 §5.3；本示例候选集为显式声明，无隐式默认候选。**

### 5.2 PA：单人普通薪俸＋物业，两候选（hypothetical 比较）

```json
{
  "tax_type": "personal_assessment",
  "schema_version": "2.0.0",
  "input": {
    "year_of_assessment": "2025_26",
    "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
    "years": {
      "2025_26": {
        "participants": [{"person_id": "p1", "role": "self"}],
        "subject_facts": {
          "p1": {"ordinarily_resident_hk": [{"from": "2026-06-01", "to": "2026-06-02", "value": true}]}
        },
        "household": {
          "marriage": [{"from": "2025-04-01", "to": "2026-04-01", "value": "unmarried"},
                        {"from": "2026-06-01", "to": "2026-06-02", "value": "unmarried"}],
          "living_apart": [],
          "child_nomination": {"person_id": null, "status": "none"}
        },
        "relations": [],
        "financials": {
          "p1": {"employment_income_nai": "250000", "mpf_mandatory_contributions": "0",
                  "has_assessable_salary_income": true,
                  "has_assessable_property_income": true,
                  "has_assessable_business_income": false,
                  "has_income_assessable_under_ordinance": true,
                  "source_classification": {"salary": "confirmed_in_scope",
                                             "property": "confirmed_in_scope",
                                             "business": "confirmed_in_scope"},
                  "properties": [
                    {"property_id": "h1", "share": "1/1", "rent_received": "120000",
                     "rates_paid_by_owner": {"amount": "0", "agreed": false, "actually_paid": false},
                     "irrecoverable_rent": "0", "deposit_offsets": "0", "mortgage_interest": "0"}
                  ],
                  "businesses": [], "deduction_items": []}
        },
        "care_payments": [],
        "claims": [],
        "assessment": {
          "reference_date": "2026-06-01",
          "basis": "hypothetical",
          "actual": {"salary_joint_elected": false, "salary_joint_assessed": false,
                      "pa": {"p1": {"mode": "none"}}, "history": "ordinary"},
          "candidates": ["no_pa", "pa_self"],
          "selection": "best"
        },
        "manual_issues": [],
        "provisional_paid": "0"
      }
    },
    "hk_presence_days": [],
    "assumptions": []
  }
}
```

- 本例子凭生日与参考日通常居港事实走 s.41(1) 年龄/居港肢；不要求无关的双亲已亡或在港日数；`actual.pa.mode="none"` 表示尚未作 PA 选择，`basis="hypothetical"` 才可比较“假设作出选择”的候选。示例是契约示例，不是已验证 fixture 或数值验收证据。

### 5.3 计划合成 fixture：单一薪俸（current 940＋partial future；**PLANNED，非运行时 fixture**）

```json
{
  "tax_type":"salaries_tax","schema_version":"2.0.0",
  "input":{
    "year_of_assessment":"2025_26","subjects":[{"subject_id":"p1"}],
    "years":{"2025_26":{
      "participants":[{"person_id":"p1","role":"self"}],"subject_facts":{},
      "household":{"marriage":[{"from":"2025-04-01","to":"2026-04-01","value":"unmarried"}],"living_apart":[],"child_nomination":{"person_id":null,"status":"none"}},
      "relations":[],"care_payments":[],"claims":[],
      "financials":{"p1":{"employment_income_nai":"240000","mpf_mandatory_contributions":"9000","has_assessable_salary_income":true,"source_classification":{"salary":"confirmed_in_scope"},"properties":[],"businesses":[],"deduction_items":[]}},
      "assessment":{"basis":"actual","candidates":["salary_separate"],"selection":"salary_separate"},
      "manual_issues":[],"provisional_paid":"0"
    }},
    "hk_presence_days":[],"assumptions":[]
  }
}
```

- 计划断言：不追问 PA 日期/history/选择；canonical input 不补这些键；current final 与已核口径一致（940）；缺未来事实 → partial、next/balance=null；改为 hypothetical 仍不补 none/ordinary。
- 活动反例（计划断言）：配偶 AI=false 而 PA mode 未知；PA hypothetical 缺 reference_date；actual PA 日期与 reference 冲突；separate 已知但 joint 转换/history 未知 → best=null 且已知 separate 不被清空。

### 5.4 计划合成 fixture：同一 YA 顺序配偶 → S7（**PLANNED，非运行时 fixture**）

```json
{
  "tax_type":"salaries_tax","schema_version":"2.0.0",
  "input":{
    "year_of_assessment":"2025_26",
    "subjects":[{"subject_id":"p1"},{"subject_id":"p2"},{"subject_id":"p3"}],
    "years":{"2025_26":{
      "participants":[{"person_id":"p1","role":"self"},{"person_id":"p2","role":"spouse"}],
      "subject_facts":{},
      "household":{
        "marriage":[
          {"from":"2025-04-01","to":"2025-08-01","value":"married","spouse_id":"p3"},
          {"from":"2025-08-01","to":"2025-09-01","value":"unmarried"},
          {"from":"2025-09-01","to":"2026-04-01","value":"married","spouse_id":"p2"}
        ],
        "living_apart":[
          {"from":"2025-04-01","to":"2025-08-01","value":false},
          {"from":"2025-09-01","to":"2026-04-01","value":false}
        ],
        "child_nomination":{"person_id":null,"status":"none"}
      },
      "relations":[],"care_payments":[],"claims":[],
      "financials":{
        "p1":{"employment_income_nai":"500000","mpf_mandatory_contributions":"18000","has_assessable_salary_income":true,"source_classification":{"salary":"confirmed_in_scope"},"properties":[],"businesses":[],"deduction_items":[]},
        "p2":{"employment_income_nai":"200000","mpf_mandatory_contributions":"18000","has_assessable_salary_income":true,"source_classification":{"salary":"confirmed_in_scope"},"properties":[],"businesses":[],"deduction_items":[]}
      },
      "assessment":{
        "basis":"hypothetical",
        "actual":{"salary_joint_elected":false,"salary_joint_assessed":false,"pa":{"p1":{"mode":"none"},"p2":{"mode":"none"}},"history":"ordinary"},
        "hypothetical_salary_joint_nomination":{"status":"named","person_id":"p1"},
        "candidates":["salary_joint"],"selection":"salary_joint"
      },
      "manual_issues":[],"provisional_paid":"0"
    }}
  }
}
```

- 预期：进入 S7；locator＝`years.2025_26.household.marriage`、year=2025_26、subject_id=p1、原因 `S7_SEQUENTIAL_SPOUSES`（**计算标签，不是权威输入**；无新 wire 码除非既有形状已允许）；不是结构矛盾；p3 已注册但**非 participant、不得成为第三 actor**。
- 本 fixture **不承诺**任何 separate 家庭金额；仅请求的 joint 无法自动确定时沿既有 manual/blocked 合同（无 prepared_id、无伪确定税额）。
- 结构负例：p3→未注册 p9、spouse=p1、两个 married 区间重叠、增加第三 participant → 结构拒绝（`E_INPUT_CONTRADICTORY`）。

## 6. 三态、金额口径与 s.26D／s.31A 桥接

- `Tri`＝原始 `true/false/"unknown"`；**键缺失＝未提供（≠false≠unknown）**；金额缺失 ≠ `"0"`；四者互不相代（C3.5 沿用）。
- 用户确认口径链：`employment_income_nai`（s.12 调整后、Part 4A 前）≠ `has_assessable_salary_income`（ss.11B–D AI 存在）≠ `has_income_assessable_under_ordinance`（s.41(1A) 全来源）。正 NAI 与 AI=false 并提 → `E_INPUT_CONTRADICTORY`；AI unknown → 夫妻判定 needs_input；不自动裁定来源。
- 服务器派生值（年龄档、路径触发、caps、entitlement、获扣除状态、候选合法性）一律由共享解析器/引擎产出，客户端**不得提交权威结论**（第二稿的 `pa_age_condition_met`/`pa_residence_condition_met`/`base_kind`/claim 内资格 facts 已删除）。

### 6.1 来源存在性一致性

- 正数、已确认应评税的物业/业务组件与相应 present=false 矛盾；不能从经扣减后的零额倒推“无该税源”。
- 已确认任一来源 present=true → 全来源不能为 false；全来源=true 而三源均 false 亦矛盾（转输入修正/来源人工边界）。
- 三源全部明确 false 才派生全来源 false；任一 true 可派生 true；显式全来源 unknown 保留并追问，不静默改写。零 NAI 不推断无 AI；来源/R2 未决时不得给确定比较或 best。
- `source_classification` 缺某“需要判定的来源分支”＝needs_input（**不得默认 confirmed**）；与候选无关、不需要判定的来源分支缺失不阻塞已证 true 的替代路径。

### 6.2 `deduction_items` 桥接与 ERCE（唯一扣除入口）

- 项＝`{item_type, amount:Money, eligibility?:{value:Tri}, subject_id?:pID|dID, payment_ids?:eID[]}`。**legacy `{value:Tri}` 包装仅在此处允许**（保留既有扣除契约的最小资格确认）；其余字段一律原始 `Tri`。
- wire `item_type` 为非空字符串；**是否支持由既有 `SUPPORTED_DEDUCTION_ITEM_TYPES` 计算集合决定**（当前支持 ERCE＝`"elder_residential_care"`；ERCE 只是显示别名，不是第二个 wire 类型），不是把所有非成员一律 enum 拒绝。**不新增阶段 2 类型，也不对未支持类型做任何计算。**
- 声明行为（保留既有兼容，见 006 R3#4）：unsupported 类型**正数** → `E_DEDUCTION_UNSUPPORTED`；unsupported **显式零** legacy 占位 → 接受（不因类型未支持而拒绝），若同时显式 `eligibility={"value":"unknown"}` → 仍 questions；unsupported 零且未声明 unknown → 沿既有无效果路径（无 questions、不影响金额）。支持的 ERCE 缺失/unknown 声明仍 needs_input/追问。
- 任何已声明条目＝扣除**意图入口**（declared intent），≠ requested：`eligibility.value=true` → 该条成为 **requested**（仍须通过原始事实派生；声明**不能**绕过 raw care/home 事实判定）；`false` → 不请求（excluded，不求解、不追问）；unknown/缺失 → questions/needs_input（即使金额正数）。
- ERCE：`amount` 必须等于 `payment_ids` 引用的本年 `care_payments` 付款合计；`subject_id` 关联长者；`eligibility=true` 只是旧式声明，**不能绕过新原始事实资格解析**（上一段 requested 规则照用）。缺失 `payment_ids` 的 ERCE 项为 needs_input，不静默按金额授予。
- `requested ≠ allowed`：requested 由上一段 eligibility 规则确定；资格成立/获计入/因互斥未获由解析输出逐条 deduction decision 声明（`requested_ineligible`/`allowed`/`not_granted_mutex`/`unresolved`＋reason_codes＋定位），输入中**没有**“已获扣除”用户事实。仅做既有 cap 计算与互斥关联，不新增扣除算法。
- **s.26D entitlement 不依赖扣除项或基本 claim**：只要 `care_payments` 原始事实（payer/subject/paid_on/amount/care 期间/院舍事实/年龄/DAA/关系）成立，服务器即可为 s.31A 派生“entitled to deduction under s.26D”；未提交 DeductionIntent、未申索基本额均不影响。反之，`residential_care_received`/`paid_to_home_or_agent` 是具体事实，`home_basis=other/unknown` 不自动认定院舍合资格；不得凭金额正数或居港事实授予。
- 父母额与 s.26D 同时提出但后者不合资格，不自动剥夺父母额；两者都可获计入时要求用户修正互斥意图（服务器不替用户选）；实际 cap 按同一 claimant/subject/YA 聚合，同 payment ID 或同长者跨人重复进入一个 scenario 时拒绝/争议转人工。

## 7. 有界时间谓词表（ANY/ALL 有限谓词，无 DSL）

```text
T(F)=明确 true 区间集合；F(F)=明确 false 集合；U(F)=允许窗口减去已确认 T/F。
ANY(P,W):  有已证 true 日期→true；所有可能日期已证 false→false；否则 unknown。
ALL(P,W):  true 完整覆盖 W→true；存在已证 false 日期→false；否则 unknown。
OR(paths): 任一路径 true→true；全 false→false；否则 unknown；缺无关替代路径不阻塞 true。
AND(paths):任一 false→false；全 true→true；否则 unknown。
SAMEPOINT(P1..Pn,W): ANY(各谓词在同一日期的 AND, W)。
```

| 条款/条件 | 固定量词（不作 universal intersection） | 来源锚点 |
| --- | --- | --- |
| s.31 子女 | `SAMEPOINT(关系有效＋存活＋未婚＋claimant维护＋(age<18 OR (18≤age<25 AND full_time_education) OR (age≥18 AND incapacitated_for_work)), YA)`；三路径**替代**，已确定一条不因无关路径缺失拒算；**无一般居港条件** | s.31；证据档 §3 |
| s.31(3) 提名 | 先求夫妻各自可成立的子女 entitlement，再按适用**整组**提名归属；不能要求获提名方实际支付每一笔供养款 | s.31(3) |
| s.31(1A)/(1B) 出生年/次年额外 | 按 `birth_date` 与键年推导；独立金额＋独立 cap；**仅当矩阵节点显式 `not_applicable_verified`（如 2024/25、2025/26 的 (1B)）才按 0 计并给理由；规则矩阵缺键＝规则不可核验 → `E_RULES_STATE_UNVERIFIABLE`，不得由数据缺失推断法定不适用**（附表 4 无条目＝来源侧法定不存在，与运行期矩阵缺键是两回事，见 §15.2） | 附表 4；§15.2 |
| s.30/30A 父母/祖父母高档 | `ANY(ordinarily_resident_hk AND (age≥60 OR (age<60 AND govt_disability_allowance)), YA)`；**DAA 只问资格不问领取**；年度供养另行判断，年龄档无需覆盖供养窗口 | s.30(1) |
| s.30(1A) 父母/祖父母 55–59 档 | `ANY(ordinarily_resident_hk, YA)`、`ANY(55≤age<60, YA)`、本年未达 60、`ALL(NOT govt_disability_allowance, YA)`；unknown 不得默认 false；**不要求居港与供养窗口相交** | s.30(1A)/L35953–35980 |
| s.30(4)(a)(i)/(ii) 供养 | 合适供养人的**年度现金** ≥ 12,000 **OR** 连续六个**日历月**非足值对价同住；**独立于年龄档**；不要求年龄资格覆盖供养窗口 | s.30(4)(a)；附表 4 L61432–61434、L61591–61596 |
| s.30(3)(b)/(3A)(b) 全年同住额外 | 基本 parent/grandparent entitlement 成立＋`ALL(cohabit_non_full_consideration, YA)`；仅额外意图时要求一并明确基本申索意图 | s.30(3)(b) |
| 六个月日历月 | 已证连续同住区间包含 `[d, 相隔6个月同日)` 即可；相邻 true 区间先合并。**月末不存在同日的精确边界仍标 OPEN**：仅依赖月末截短约定才能成立的个案转 manual，不自行发明法定计月规则（窄边界，不整类禁算） | 协调者冻结前选择/保留 |
| s.30B 兄弟姊妹 | 年内 `ANY(未婚 AND 受供养 AND 年龄/教育/伤残路径, YA)`；`ANY(sole_or_predominant_care, YA)`；按 s.30B 分别量化，不把全部事实强行同点相交 | s.30B |
| s.32 单亲 | 对指定子女有 child entitlement（**不要求 child claim 存在**）＋`ANY(sole_or_predominant_care, YA)`；每 claimant/scenario 至多一次 | s.32 |
| s.32(2) 单亲排除 | `ANY(married AND NOT living_apart, YA)` 为 true 即排除；**后来分居不能抵消**；unknown 且影响资格则追问 | s.32(2)；L36472–36478 |
| s.28A 个人伤残 | `ANY(govt_disability_allowance, YA)`；subject＝claimant | s.28A |
| s.31A 伤残受养人 | `ANY(subject.govt_disability_allowance, YA)` AND 对应 child/parent/grandparent/sibling/spouse entitlement；不要求基本 claim 或扣除意图存在 | s.31A(4) |
| spouse DDA | s.29 分支 A、合法 joint salaries 或 joint PA 对应的 married entitlement **任一路径**成立即可，不只支持“配偶无 AI” | s.29；D:401 |

- 生日前后、未婚/供养部分年度、教育过 25 岁生日的窗口均按实际交集求值；不只用年度末年龄。
- 死亡/争议事实影响总额时，保留无依赖的已知组件，不把未知 allowance 当零再给确定 final；死亡年度、争议分配、复杂来源、未核亲属定义**可提交、可定位转人工，不整类禁算**。
- TF 覆盖面：默认仅键年 `[4月1日, 次年4月1日)`；仅 `ordinarily_resident_hk`、`both_parents_dead`、`marriage`/`living_apart` 可额外覆盖 assessment 参考日及已声明 PA 选择日，每个外部日期只开放该日的一天区间，不开放任意跨年事实复制。**唯一其他年外例外＝`care_payments[].home_status_in_force`：其合法域恰为该付款的 `[care_from,care_to)`（可完整落在付款 YA 之前，即使早于键年），不受默认 Year 窗口截断；除上述列明项外不开放任何其他年外 TF。** 生日之前的年龄/存活资格由服务器判不成立，不要求填“出生前未婚”。

## 8. Claim ledger 矩阵（同一 scenario 内执行；PA/no-PA ledger 不互相消费）

### 8.1 重复与互斥（键＝`(kind, claimant_id, subject_id)`＋类规则）

| 规则 | 判定 |
| --- | --- |
| 同 `(kind,claimant,subject)` 重复条目 | `E_INPUT_CONTRADICTORY` |
| 同 subject 跨 claimant 重复同类申索 | `E_INPUT_CONTRADICTORY`（s.31(3) 整组提名例外；`child_nomination` 对账） |
| parent＋grandparent 同 subject；child＋sibling 同 subject | 类互斥 → `E_INPUT_CONTRADICTORY` |
| 可叠加：child＋disabled_dependant、parent/grandparent＋cohabitation_additional | 合法叠加，不误判重复 |
| 同人 parent/grandparent 额与 s.26D 获扣除 | 同 scenario 互斥（s.30(5)），按 §6.2 判定 |
| 跨方案复用 | 同一合法事实在 PA 与 no-PA 方案各自 ledger 复用不算重复 |

### 8.2 互斥决策顺序

entitlement（关系/年龄/DAA/供养路径）→ 叠加合法性 → 类互斥 → 提名/归属 → 扣除互斥。纯申索意图不授予 entitlement；`kind` 仅作意图与 ledger 引用/对账，不派生资格；`base_kind` 已随第二稿删除，本模型无该字段或角色。

## 9. 方案真值表与候选择案（有限方案；非阶段 3 全枚举）

### 9.1 薪俸税夫妻（s.10/s.29）

| # | 事实组合 | 处理 |
| --- | --- | --- |
| S1 | **明确未婚** | 分别评税、基本额（s.10(1) 默认）；婚姻缺失/unknown 且已婚额、联合或 PA 分支可能成立 → needs_input（S7），**不得默认 single** |
| S2 | married＋配偶 `has_assessable_salary_income=false`＋配偶未单独选 PA | s.29(1)(a) 分支 A：**已婚免税额成立**（不要求已选联合）；无联合选择 ≠ 只给基本额 |
| S3 | married＋双方 AI 存在＋未选联合 | 分别评税、各基本额；无已婚额（s.29 分支均不成立） |
| S4 | married＋（actual：`salary_joint_elected=true`／hypothetical：假设作出选择）＋**已核普通婚姻窗口成立**（见 S6）＋双方 AI＋经 §9.1.2 两遍流程接纳 | 联合评税合法：单一 NCI＝两人 NAI 合计 − 两人 Part 4A − **一份**已婚免税额（s.12B(2)）；(a)/(b) 严格不等号；**(a) 成立时不得因 (b) 不成立排除**；(b) 使用 §9.1.2 的独立 salary nominee |
| S5 | 选择联合但 (a)(b) 均不成立 | 候选标 `ineligible`＋排除原因；不输出为 best |
| S6 | **已核普通婚姻窗口**（本阶段自动处理边界，不是宣称法例要求全年已婚/全年未分居）：YA 全域明确 unmarried；或全域同一婚姻且全部婚姻期间明确 not apart；或一次明确 unmarried→同一配偶 married、婚后至年末明确 not apart（s.10(5) 全年合并、不按月份缩减） | 按普通路径自动处理；**删除“年度 ANY 分居→joint/分支A 不成立”的旧硬否决**；`living_apart` 不以年度 ANY 事件全局化 |
| S7 | 存在分居、复合、离婚、换配偶（含同一 YA 内已注册、排序、非重叠的顺序配偶变化），或有关期间缺口；或婚姻/配偶 AI unknown 且涉 S2–S6 | 仅将**依赖这些条件的** joint/s.29/婚姻相关 PA 及归属决定置 needs_input/manual/unresolved（locator＝`years.<YA>.household.marriage`，year/subject_id/field 定位；原因标签如 `S7_SEQUENTIAL_SPOUSES` 为计算标签、非权威输入字段，除非既有形状已允许则不新增 wire 码）；条件可独立确定的 separate 组件照常输出；**不得**以 NAI=0 或扣 MPF 后为零推断 AI=false；婚姻缺失不得默认 single；声明 spouse 但婚姻全 unknown＝结构有效＋needs_input |

- **仅子女未婚/供养/教育为部分年度不触发夫妻窗口 manual**（照 §7 TF 量词）；单亲排除 `ANY(married AND NOT living_apart, YA)` 保持原样，不随本表改动。

#### 9.1.1 基础额与申索权归属矩阵（自动 basic/married 跨人互斥、精确评估单位）

| 上下文 | 基础额归属 | child 及 child DDA | spouse DDA |
| --- | --- | --- | --- |
| `salary_separate`／no_pa 薪俸单位 | s.29 分支 A 成立时 married 给对应申索人并覆盖两人基础额；另一人不得再获 basic/married，否则各适用单位 basic | 非分居夫妻先求子女原始资格，再由 child nominee 整组持有；**无基本 child claim 的 DDA 也使用同一归属** | 仅该人有 s.29 entitlement 时成立，不能只凭婚姻 |
| `salary_joint`—s.10(2)(a) | 一个 joint 单位、一份 married；责任人由分别路径唯一原本应课税者派生 | child nominee 归属不变，允许金额汇入 joint 单位一次 | 依该责任人的 married entitlement |
| `salary_joint`—s.10(2)(b) | 一个 joint 单位、一份 married；责任人为独立 salary nominee | 同上；child nominee 可与 salary nominee 不同 | 依 salary nominee 的 married entitlement |
| `pa_self`＋spouse no_pa | 按候选有效 PA 选择重判 s.29；self 的 individual PA 影响 spouse 分支 A；基础额跨人去重 | nominee 归属仍有效；每笔只进入所属人的 PA 或剩余非 PA 单位之一 | 按该候选实际成立的 s.29 entitlement，不借用其他候选 |
| `pa_joint` | 一个 joint PA 单位、一份 married 覆盖双方，禁止再加 basic | 依既定提名归属后汇入 joint 单位一次 | 按 s.29(1A) 对应双方 entitlement，不虚构 salary nominee |

- 零薪俸 AI 且无 PA 承接的单位，不因逐人运行 S2 生成两份 married；确无税可保留零税结果，但不得编造基础额已获归属。
- 原始资格（raw eligibility）≠ 候选 entitlement ≠ 最终 granted；basic/married 为服务器派生基础额（无客户端资格 flag）；**salary nominee 与 child nominee 是不同字段**；child DDA 依 child 整组 nominee，spouse DDA 依 s.29(2) 责任人。
- s.10(2)(a) 成立但责任人不能唯一确定：**仅该 joint 归属决定 unresolved**（needs_input），不猜 self，也不清空独立已知的 separate 结果。

#### 9.1.2 有限两遍流程（无递归/固定点）

```text
前置：验证普通婚姻窗口（S6）、双方 AI、实际/假设选择一致性及必要归属事实。
第一遍：在“分别薪俸”上下文解析各自 Part4A/Part5、child 提名及实际 grant；
        计算每人 NAI、分别 NCI、分别最终税；
        A := 任一方分别路径 Part4A＋Part5 > 其 NAI；
        B前提 := 双方分别 NCI > 0。
第二遍：A=true → 采用 (a) 派生责任人；否则 B前提=true → 采用 (b) 独立 salary nominee；
        建立仅内部有效的“假设 joint”上下文；
        在该上下文解析一份 married 及依赖它的 spouse DDA，计算 conditional joint final；
        B := B前提 AND separate final 合计 > conditional joint final；
        前置 AND (A OR B) 成立才接纳 joint；否则 ineligible/unresolved。
接纳后，条件式 entitlement/grant 才转成该候选的正式决策。
```

- 条件式试算**不是隐式加入请求候选**；**未接纳 joint 的 `conditional_final` 只能出现在 §14 `candidate_checks[]` 的非权威检查记录中**（label 明确“条件式比较，非已接纳税额”），不得填入其 scenario 六金额键、selected 或 best；拒绝候选不留下貌似已授予的 married/DDA decision。
- 比较范围仅本请求已声明事实与申索安排，不自动优化未提出的其他免税额；不做通用求解器。

#### 9.1.3 具名 T3 计划（手算，**未执行**）

`test_002_joint_spouse_dda_conditional_admission_no_cycle`：2025/26，NAI 500,000/200,000、MPF 各 18,000、双方 AI、p2 DAA 已证、仅 p1 申索 p2 的 spouse DDA 75,000、hypothetical salary nominee=p1：separate＝41,500−3,000＋1,000−1,000＝**38,500**；joint 不计 DDA＝50,000−3,000＝**47,000**；conditional joint 计 DDA＝37,250−3,000＝**34,250**，(b) 成立。反向案例：改变 nominee 使 claim 不属责任人时不得继续沿用 34,250（错误授予负例）。上述为手算说明，不是运行结果。

### 9.2 PA（s.41/42A/43）

| # | 条件 | 候选 |
| --- | --- | --- |
| P1 | 参考日 **(`age≥18` OR `both_parents_dead=true`)** AND（该日 `ordinarily_resident_hk=true` **OR** temporary 路径：本 YA 在港>180 天，或本 YA 与相邻任一 YA 合计>300 天；严格大于）——两组括号并列，**temporary 路径不能绕过年龄肢** | `pa_self`/`pa_joint` 的年龄/居港两肢须同时成立 |
| P2 | **married＋非分居**（以已核适用事实在该候选参考/许可选择日求值）＋**双方** `has_income_assessable_under_ordinance`=true＋一方或双方 P1 | `pa_joint` 合法候选；**单凭收入不得联合**；非分居的精确时点/窗口超出已核普通范围时＝窄 manual＋reviewer question，不得以年度 ANY 事件全局化禁用 |
| P3 | 任一方已按 s.10(3) 联合薪俸评税 | 单独 PA 被禁，除非双方共同选择（s.41(1B)）；候选排除＋理由 |
| P4 | 任一条件 unknown | 该候选 needs_input；不伪造合法候选；`no_pa` **不是隐式候选**——仅当 `candidates` 显式列入才可比较（非空、不隐式添加，§9.3） |
| P5 | `actual.pa` 选择与候选合法性冲突 | 矛盾/人工核实；不宣称政府选择无效、不回退默认；withdrawn/disputed → 相关候选 unresolved；仅“本次活动 actual joint PA 选择日 P2 已知反证”的窄澄清见下表后段落 |

- **P5 窄澄清（仅限本次活动 actual joint PA 的选择日存在 P2 已知反证）**：当该选择日的婚姻/分居事实已知、且显示 P2（married＋非分居）在选择日不成立（已知未婚、或已知分居）时：**仅相依的 joint 候选**置 unresolved/manual（按 §13 既有定位形状由选择日婚姻/分居事实定位人工核实，不新增 wire 键），`best=null`，**不产出任何确定的 joint 税额/不留 pa_joint 确定 scenario**。若另有**真正独立、已知且合法**的显式 selected 结果，按 §9.3 既有规则保留；**不为保留它强造违反 actual 模式合同的候选**。不得仅因此以 `E_INPUT_CONTRADICTORY` 拒绝整份输入、不得宣称政府选择无效、不得自动判不合资格；不得**隐式**加入 `no_pa`，也不得在 actual 模式合同不允许时把 `no_pa` 宣告为 eligible。其他 actual 配对 mode/日期冲突、转换一致性、hypothetical 规则**不变**。

### 9.3 有限候选：有效选择、actors 与单位覆盖（B3）

每个候选派生只读内部 `EffectiveAssessment = {salary_mode:"separate"|"joint"|"absorbed_by_pa", pa_actor_ids:[pID], pa_by_person:{pID:EffectivePAEntry}, responsible_person_id:pID|null, assumed_elections:[string]}`；`EffectivePAEntry` 与既有 actual PA 条目**同一形状/同一日期校验**：`{mode:"none"|"individual"|"joint"|"unknown", date?:Date}`。Effective* 仅服务端内部派生（**不是新 wire 字段**），不得修改 actual；actual 冲突/转换判定与本节表仍为权威。

| 候选 | 有效 salary 模式 | 有效 PA（`pa_by_person`） | 必须覆盖的单位 |
| --- | --- | --- | --- |
| `salary_separate` | separate | **从 `actual.pa` 复制实际已提交条目到内部 `pa_by_person`（缺键保持缺键，不补 none/unknown；不代表 actual 存在 `pa_by_person` 新 wire 字段）**（仅用于相关 s.29 判断；本端点不计 PA 税） | 两人各自薪俸单位 |
| `salary_joint` | joint（须 §9.1.2 接纳） | **从 `actual.pa` 复制实际已提交条目（缺键不补）**；不假造 PA 选择；actual 冲突另核 | 一个联合薪俸单位 |
| `no_pa` | **separate** | 每人均 `none`（派生，不改 actual） | 两人非 PA 薪俸＋全部相关物业/业务单位 |
| `pa_self` | separate＝**仅剩 non-PA 薪俸单位**；self 的 salary 经 PA 吸收，不再另计 separate salary tax；已受 s.10(3) 约束则不合法 | self `individual`（日期按 §4.10 规则 5/6：basis=actual 取实际选择日期、basis=hypothetical 取 reference_date；wire 可选、提供即严格校验）、spouse `none` | self 全部来源进入 PA 一次；spouse 剩余非 PA 单位一次 |
| `pa_joint` | **absorbed_by_pa**（PA 吸收双方来源，不另计薪俸税；**不得虚构 `salary_joint_elected=true`/`salary_joint_assessed=true`**） | 双方 `joint`（日期按 §4.10 规则 5/6：basis=actual 取实际选择日期、basis=hypothetical 取 reference_date；wire 可选、提供即严格校验，s.29(1A)） | 一个 joint PA 单位；无重复 salary/property/business final 税 |

- `basis=actual` 必须与该行**全部**有效模式一致（不只比较 PA mode）；**actual no_pa＋joint salaries 本阶段 unsupported**（不静默拆成 separate；仍可经 salary_joint 端点取得已支持薪俸结果）。`pa_self` 的 s.29 判断必须看到 self 已 individual PA（spouse 不得继续以原 `actual.pa[self]=none` 作分支 A 依据）。hypothetical 的“拟议选择”若需撤回/转换真实选择而合法性未明 → 该候选 unresolved，actual 保持原样。
- **单位覆盖纪律**：先按原始 hID/bID 识别同一资产/业务及每人份额、检查共享原始值一致，再分配到候选单位；禁止先按人切片后各自计算整单位税。**所有相关权益都进入 PA** 时按既有个人份额规则汇总；**全部仍属 no_pa** 时按已批物业/业务评税单位及既有普通合伙份额契约处理；两者均不重复整单位税/同一业务宽减。
- **同一单位横跨 PA actor 与 non-PA actor、且现有已批算法不能给出剩余税额分配**：**仅该 mixed 候选** `unsupported`（原因 `CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED`），作为**未决竞争者**参与 best（不得当法定 ineligible 删除）；其他已知合法候选保留。不得用 self NAV 份额进 PA＋spouse 承担整物业税，也不得用产权/利润比例乘税额发明分配。
- 不因混合限制禁用 `pa_joint`、完整 `no_pa` 或既有普通合伙入口；独立单位分属两人时 mixed 可继续；资料不足＝needs_input，未实现分配＝unsupported。不增加 pa_spouse、双 individual PA 或其他组合。
- `candidates` 显式/非空/无重复、不隐式添加；salary 请求仅前两种、PA 请求仅后三种；混合 `pa_self`＋spouse no_pa 为 G2 决定项。
- `selection` 必填：`best` 只有一个含义——请求集合**全部可判**后选择已证合法最低本年 final；**任何可能竞争的 unknown/unresolved 候选存在 → best=null**。同税额产品择案顺序不变（salary 优先 separate；PA 与 no_pa 平局仍优先 PA；`pa_self` 与 `pa_joint` 平局优先 `pa_self`；展示确定性，不是法定优先权）。
- 显式选择已知合法候选可输出该候选 final，同时 best 仍可为 null；`selection=best` 而不能确定 best 时保留各已知 scenario，top-level final=null。
- 全候选明确不合资格 → blocked；必要资料不足 → needs_input；有独立已知 scenario 但未择案（含仅 mixed 未决） → partial。

## 10. 组件适用与状态表（C3.3/C3.4 细化；状态增量供 Gate 2）

| 组件 | 薪俸税 | PA（本阶段） |
| --- | --- | --- |
| before_reduction/reduction/final_after_reduction | 事实确定即可算 | 事实确定即可算（Part 5 只降累进基数） |
| `provisional_paid` | 可选事实；缺失 → null＋missing | 读入；缺失 → null＋missing |
| `balance` | **任何 partial 一律 null**；仅 complete 且 paid 已知时按已批本年 final−paid（不含 next；缺 paid → null）；**结果可为负＝估算退税（C2 有符号 AmountStr；不得据负值放开其他输入为负）** | **恒 null**（跨税项缴款/退款比较属阶段 3） |
| `next_provisional` | 目标年规则/事实/guard 齐 → 计算并记假设；否则 null＋missing | 同左 |
| status 上限 | 齐全 → `complete`；否则 `partial` | **本阶段恒 `partial`**（`balance`/`payment_comparison` 范围外） |
| `best_scenario` | 仅依所列合法候选**本年 final＋资格**是否确定；**独立于 paid/未来暂缴**；任何可能竞争 unknown 候选 → null（§9.3） | 同左 |
| top-level `amounts` | 属已保存 **selected** scenario；显式选中已知候选时其 final 可保留，即使 best=null | 同左 |
| `blocked` | 按 C3.3/C3.4：**六个金额键一律显式 null、键不省略**，附 blocked_reason＋错误形状 | 同左 |

- `complete`＝本结果定义的全部组件确定；本年必要资格 unknown → needs_input；规则不可核实／范围外核心事实 → blocked（不产生确定总税额）。null 不显示为 0，未知不拼确定总额。

## 11. 下一年度、具名假设与可用性

```text
Assumption = {assumption_id:aID, kind:"same_income"|"same_mpf",
              person_id:pID, source_year:YA, target_year:YA}
```

- 仅此**两种**假设；最多每人每 kind 一条；`target_year` 必须紧邻 `source_year` 且在请求允许的 `years` 中；该条本身表示用户明确确认产品投射。
- `same_income` **仅**复制该人的 `employment_income_nai`；不复制物业、业务、利息、来源存在性、扣除、家庭关系、role、选择、claim 意图或 TF 日期。`same_mpf` 仅复制该人的 MPF 金额；下一年按下一年 cap 重新计算，不复制已扣金额。
- 显式目标金额与假设同额：以显式事实为主，trace 注明假设冗余；**不同额 → `E_INPUT_CONTRADICTORY`**（不采用“最后写入者优先”）。
- 其他下一年事实必须显式提交；不存在泛用 same_facts／自动继续婚姻／自动复制 role/申索/选择的假设。
- 下一年缺事实/规则/guard 许可：next=null，**保留当前已知 final**；**所有 salary partial 的 balance 均 null**；PA 亦恒 partial、balance null。目标年规则可用时**重新解析原始事实**（年龄档、出生额外额、各独立 cap、资格与方案合法性），不得复用本年解析对象。
- HTTP 预检、HTTP/AI 执行与 v2 纯引擎均收 §2 的显式 AvailabilityContext（pinned binding＋current/next）；缺许可不默认未来计算；引擎不联网、不回退规则。replay/reference/report 的来源与失败优先级按 §2.1–§2.2：**兼容 replay 永远使用原 binding/原 bundle**；reference/report 使用受信 case descriptor 的显式 context，本地标注为固定束核验/模拟而非实时可用性。

## 12. 服务端修订协议、原子消费与完成（G2 协议 delta）

> 本节为**已经用户 Gate 2（2026-10-14，范围仅 stage1）明确批准**的七税公共确认协议增量；其余五税 input schema/算法不变，但全部 prepare/execute 调用方（含测试/脚本）需适配该显式 delta。运行时 `session_epoch`/`input_revision` 只从 meta/version 响应或本操作 ACK 读取，**不写入 binding/core**。

### 12.1 prepare reservation（唯一入口）

- 由 SecurityMiddleware 对 `POST /api/v1/calc/prepare` 调用**一次**内部 `SessionRegistry.begin_prepare(sid, expected_epoch, expected_revision, consent_store)`；**route 不得再次递增 revision**。middleware 与 routes 共享同一 ConsentStore 实例（create_app 装配、显式传入；不新增服务/端点）。
- 固定顺序：Host → Origin → 有效 session/CSRF → `If-Match` 语法与 CAS → 原子 reservation（revision++；失效旧 prepared/未消费 confirm/该 sid 未消费 consent）→ Content-Length 上限检查 → 有界 stream 读取（累计 >1MB 即 413；超限 chunk 不得进入聚合 buffer）→ JSON/业务校验 → 保存 prepared 前再次核验 reservation 仍当前。
- 认证与 CAS 前不调用 ASGI receive、不解析 body；reservation 置于可信 ASGI state，不从 body 恢复。

| 请求 | HTTP/效果 |
| --- | --- |
| Host/Origin/session/CSRF 拒绝（即使声明超大） | 403；不读 body、不 CAS、不返回版本 ACK |
| 已认证，`If-Match` 缺失/非法/陈旧（即使声明超大） | 409 `E_CONFIRMATION_STALE`；不读 body、不推进 revision |
| 已认证、If-Match 匹配、声明 >1MB | **先推进并失效，再 413 `E_INPUT_OVERSIZE`**；不读 body |
| 已认证、匹配、分块累计超限 | 已推进；413；旧能力不恢复 |
| 已认证、匹配、非法 JSON/字段/needs_input | 已推进；422 及既有问题；无 prepared_id |
| reservation 后另一编辑/clear，再保存 prepared | 409 stale；不落 prepared；不重用/不补发 |
| reservation 后内部错误 | 保留失效效果；安全错误/500；能响应时附本操作 ACK |

### 12.2 固定运行时 ACK 头（本操作 ≠ 当前版本）

- `X-Edit-Applied: 1|0`＝本请求是否成功修改 revision；`X-Edit-Operation-Epoch`/`X-Edit-Operation-Revision` **仅 Applied=1 存在**，来自本请求 reservation/invalidate 结果，**响应结束时不得换成 registry 最新值**；`X-Edit-Current-Epoch`/`X-Edit-Current-Revision`＝已认证 409 时的当前只读快照（冲突信息，**不是自动接纳另一 editor 的授权**）；全部 no-store；403 不带上述版本信息；`_send_error` 与正常 send 路径共用注入（含 middleware 早退 413）。
- 同一 editor 串行队列（与 004 §7/flows 18/19/21 最终语义一致，队列覆盖 **invalidate 与 prepare**）：**同一 ACTIVE operation＋同一 live epoch 的有效 Operation ACK 推进“内部已确认版本”，即使本地 generation 已变**；其 stale payload/errors/prepared/result 一律丢弃、不得覆盖最新草稿、不得取得权威。**旧 epoch／已取消 operation／显式重同步之后的 ACK 整体忽略**；Current 头只读、不自动接纳、不自动 prepare/重试。422/413 也确认“本次失效已发生”，但不得复活 prepared；响应丢失保持 blocked、不猜测；同标签页仅一个 mutation 在途，在途编辑合并 dirty generation，不用未 ACK 旧 revision 并行发请求；跨标签页真 409 显式重新同步。

### 12.3 原子消费、执行快照与完成（补充契约）

```text
ExecutionContext = {execution_id, session_id, session_epoch, input_revision, confirmation_id,
  tax_type, schema_version, execute_target, input_hash, canonical_input_bytes,
  binding, verified_bundle_bytes, availability_context}
CompletionReceipt = {outcome:"stored"|"already_stored"|"discarded", record_id|null,
  core_hash|null, execution_epoch, execution_revision,
  editor_result:"current"|"historical"|"discarded"}
```

- `consume_for_execution(sid, confirmation_id, target, store, guard)`：在 registry→store 锁序内验证会话/目标/epoch/revision/TTL/schema/tax/hash/binding（prepared 必须存在且一致，**禁止 `facts={}` 回退**）→ 装入并核验完整 bundle hash/schema/固定 profile＋guard 快照（current 阻断或缺必要 context 拒绝；next 阻断仅写入快照）→ 创建不可变 ExecutionContext 并在同一临界区登记 execution_id 与 consumed/running；**此前任何失败均不消费**。锁内无网络、无引擎运行；释放锁后计算，**消费之后禁止再次 get_prepared 或重读 current bundle**。
- 引擎只从快照解码**独立工作副本**；record 输入/binding 取快照，不取引擎修改后的 facts。`availability_context`：两税含 current/next 与绑定；其他五税保存既有当前期 guard 快照、next 不适用。
- 完成登记的授权到期点取本次已消费计算确认 confirmation_id 在签发时确定的 `created_at + ttl`（同一 monotonic 时钟），不取 prepared 或 AI consent 的期限；consume 不刷新该期限。该值由 registry 权威 execution entry 独立保留，不扩展 ExecutionContext/core/binding。对于尚未保存的首次完成，在 record/link 原子提交前于临界区核验：`now > expires_at` 时返回完整 discarded Receipt，HTTP409 E_CONFIRMATION_STALE，不保存 record/link、不返回 core/税额；等号尚未过期。已成功保存的完成仍按原幂等／冲突合同处理，不因该确认期限随后到期撤销记录；clear/session/epoch 失效优先。响应在途时间不参与此判断。
- `complete_execution(context, core)`：核对所属 session/epoch、execution 身份及 core 的 tax/input hash/binding 后**一次性**保存 record 与 consumed 关联并返回 Receipt（record_id 由 registry 分配一次；**禁止 `confirmation["record_id"]=…` 或修改 getter 副本**）。`fail_execution(context, safe_error_code)` 仅把仍属该 epoch 的 running 执行标 failed，不恢复能力、不写税额。`get_consumption(sid, confirmation_id)` 返回只读副本，仅用于重复消费响应/record 链接，不参与重算。
- 交错表：错目标/current guard 失败 → 原错误且不消费；消费后编辑 revision++ → 仍按快照存历史 record（editor_result=historical）；规则/guard 变化同理；**clear/过期/epoch 变化 → Receipt=discarded，无 record/链接写入，不得 ensure_session 或重建旧 session**；同 execution 同 core hash 重复完成 → already_stored＋原 record_id；不同 core hash → 外层 409 `E_CONFIRMATION_CONSUMED`，原 record 不变；再次 execute 已消费 token → 409，仅同会话有效已完成关联才附 record_id；计算抛错 → 标 failed 且仍 consumed，不自动重跑；clear 后旧 token 不返回旧链接。
- 完成登记先检查当前 session 存在且 epoch 等于 context 再处理幂等；**`editor_result="current"` 仅当当前 revision、binding 及相关 guard 仍匹配 context；否则只能 historical**；clear 之后即使旧调用缓存成功 Receipt 也不得写回。discarded 对 HTTP 映射 **409 `E_CONFIRMATION_STALE`**、AI 条目 **rejected 同码**，且**不返回旧 core/税额**（不是“仅不写 record”）；HTTP/AI 响应可附运行时 `execution:{session_epoch,input_revision,editor_result}`——**该运行元数据在十一 core/四 binding 之外**。快照消费/fail/record 链接语义不变；clear 的 ACK 仍为已分配的 epoch+1/rev0＋既有 body，不靠事后 current 查询。
- 调用方适配范围：旧 HTTP route（原写 `confirmation["record_id"]`，`routes.py:277–279`）、AI tools（原先消费后读 prepared，`tools.py:329–336,340–342`）、getter、session clear 与失败路径，全部按上述 API 迁移；不得只改方法名。
- 清空会话扩展：`DELETE /api/v1/session` 原子保留 sid、`session_epoch+1`、`input_revision=0`，并在既有锁序下删除旧会话状态/未消费能力/该 sid consent；新版本经同一 Operation ACK（Applied=1）返回，不靠事后 current 查询；保留既有 clear body；**clear 是 epoch 边界，不是 revision++ reservation**；ACK 丢失须显式重同步，不恢复旧 record/能力（G2 提案）。
- **invalidate 端点与 in-flight 边界**：`POST /api/v1/calc/invalidate` 闭合请求 `{expected_session_epoch, expected_input_revision}`；CAS 成功 200 `{status:"invalidated", session_epoch, input_revision}`（rev++），删除旧 prepared、失效未消费 confirm 与该 sid prepared/granted consent、保留 records；CAS 失败 409 `E_CONFIRMATION_STALE`。保证自服务器线性化点起；已消费发送的 in-flight 字节不可撤回（发送前原子 consume 是提交点）；迟到 AI 响应在处理 tool_calls 前重新核验该轮 epoch/revision/consent，过期整轮丢弃；迟到本地计算只能存历史 record，不能成为当前 editor 结果。consent grant/consume 采用预期状态＋epoch/revision 原子比较，禁止把 stale/consumed 恢复为 granted。
- UI 编辑/采用提案：立即禁止当前 confirm/execute/send，提交队列内 invalidate；ACK 后才允许新 prepare；网络失败保持 dirty/不可执行，不假装撤销成功。

## 13. 错误与定位形状

- **422（结构/资格）**：`{"error": {"code","message_zh","field","details","binding"}, "questions":[…], "status":…}`（errors.py `to_payload`＋extra 合并现状）；`questions[]` 项含 `{field, year, claim_id?, subject_id?, question_zh}` 供 UI/聊天定位；needs_input/blocked 均**无 prepared_id**。
- **409**：`E_CONFIRMATION_TARGET_MISMATCH`（错目标，不消费）、`E_CONFIRMATION_STALE`（invalidate CAS/confirm 版本不符）、`E_BUNDLE_INCOMPATIBLE`（replay 引擎不匹配）。
- 语义码沿用 C3.7；`E_INPUT_CONTRADICTORY` 扩展至 §8 ledger 冲突与 §6.1 来源矛盾；`E_RULES_STATE_UNVERIFIABLE` 扩展至旧/不兼容 bundle 拒算；未知资格保持 `E_ELIGIBILITY_UNKNOWN`＋questions。
- **parsed v2 input 的 `error.field` 完整路径规则（CF1；不改既有 JSON 语法/信封/安全/确认/内部 error 可空字段合同）**：对已解析 v2 input 事实内的可定位结构/类型/引用/域错误，`error.field` 使用**完整 input 内容根路径**（自 `input` 内容起算，**无 `input.` 前缀**）：对象层级用 `.`、数组用 `[index]`、`Year` 子路径以 `years.<YA>...` 起；`E_INPUT_EXTRA` 必须包含**违规键本身**。示例：`years.2025_26.participants[0].person_id`、`years.2025_26.financials.p1.extra`、`hk_presence_days[0].year`。整个 `input` 类型失败时 `field="input"`；**根级可选数组不得虚构到 `Year` 之下**（如 `hk_presence_days[index].year` 保持根路径）；缺失字段指向**预期完整路径**。既有 `questions[]` 定位字段（`field/year/claim_id?/subject_id?`）形状不变，不新增字面量索引/英文要求、不新增内部身份 locator 或 error 对象键；除 CF3 明示（§4.7 `hk_presence_days[].year` 域外 → `E_INPUT_EXTRA`）外**不重新分类错误码**。

## 14. 引擎 steps trace 规范（确定性 JSON，可哈希；保存只读）

- steps 为**有序数组**：`[{step:int, label:str, kind, detail}]`；kind 仅 `context,decision,assessment,scenario,selection,next_year`。detail 按 kind 封闭：

```text
context:    {year,tax_type,profile_id,assumptions:[已规范假设]}
decision:   {year,scenario_id,claim_id:null|string,claimant_id,subject_id,
             item_kind,entitlement:"eligible"|"ineligible"|"unknown"|"manual",
             grant:"allowed"|"not_requested"|"ineligible"|"mutex"|"unresolved",
             amount:Money|null,reason_codes:[string],rule_refs:[string]}
assessment: {year,scenario_id,unit_id,branch,actors:[pID],
             bases:[{name,value:Exact}],operations:[Op],
             amounts:{before_reduction,reduction,final_after_reduction},
             shares:[{person_id,ratio:Exact}]}
scenario:   {year,scenario_id,status,unit_ids:[string],amounts:六金额键}
selection:  {year,requested:[CandidateID],selected:CandidateID|null,
             best:CandidateID|null,reason_codes:[string],
             candidate_checks:[{candidate_id,
               admission:"eligible"|"ineligible"|"unresolved",
               legal_path:null|"s10a"|"s10b"|"pa",
               responsible_person_id:pID|null,
               separate_final:Money|null,conditional_final:Money|null,
               reason_codes:[string]}]}
next_year:  {year,status:"available"|"unavailable",selected:CandidateID|null,
             amount:Money|null,reason_codes:[string],assumptions:[已规范假设]}
Op:         {op,inputs:[Exact],output:Exact}
```

- `Exact` 始终约分字符串 `num/den`（整数写 `3940/1`；分母正；零写 `0/1`）；Money 用 canonical 定点字符串；禁止 Fraction 对象、float、运行时 ID（record/prepared/confirmation/consent id 不入 trace）。
- `bases.name` 限 `nai,part4a,allowances,nci,reduced_income,nav,nav_share,profit_share,standard_base`；`op` 限 `progressive_tax,standard_tax,floor,ceil,choose_floor_min,choose_standard_lt,rebate_min,subtract,add,profit_low,profit_high,ratio`；inputs 按分支运算次序排列，不提供解释执行器。
- `step` 从 1 连续；排序为年度→候选固定顺序→单位 ID→decision 类别/claimID/subjectID；运算数组按实际执行顺序；同一输入数组规范化后同一顺序；reason_codes/rule_refs 排序去重；label 由固定模板生成，无原始用户文本；旧记录缺 label 显示固定“计算步骤”，null 显示“未能估算”，不改 core。
- `candidate_checks[]`（§9.1.2）：**未接纳 joint 的 `conditional_final` 只出现在此非权威检查记录**（label/说明明确“条件式比较，非已接纳税额”），不得进入 scenario 六金额键、selected 或 best；正式 decision 仅在接纳后写 `allowed`。
- **实际分支表（按执行位置记录，不按“PA”泛化）**：

| 分支 ID | 实际顺序 |
| --- | --- |
| `salary_floor_min_v1` | P、S 分别 floor→比较，平局累进→外层 floor（显式执行即记录）；rebate=`floor(min(before×rate,cap))`→相减 |
| `pa_unit_floor_min_v1`：individual | PACOut 路径：P floor；标准 helper 返回 floor(S)；取 min，平局累进；rebate ceil→相减；不宣称经过 CompTP |
| `pa_unit_floor_min_v1`：joint | 保留现有 PA 评税单位的 floor/min 链；合并基数、一份宽减；宽减后按 Ri/ΣR 记录精确比例，ΣR=0 不除法（**现行产品约定，不是已独立观察的官方 joint 分支**） |
| `pa_stc_comp_tp_v1`：no_pa 薪俸子分支 | **仅**适用 D6.2a 的 STCOut/CompTP 路径：按真实 callee 返回值严格比较，标准较低才选标准、平局累进→外层 floor；标准 helper 内部 floor 单独记；rebate ceil |
| `property_per_assessment_floor_v1` | 逐物业 `(AV−允许差饷)×(1−repair_ratio)`→NAV floor→税额 floor；无宽减；不得按人重复整物业税 |
| `approved_unincorporated_floor_branch_v1` | floor(利润份额) 用于择档；低档时全部 raw 利润份额×低率，高档才分段→税 floor→rebate ceil→相减/既批最终 floor |

- 前三年度既批税率、cap 及最终 floor 不改变；其他五引擎不改算法；每事件在对应运算完成后产生，禁止 records 事后重造链。规范示例（合成 Q1，**未执行**，只列实际所需单一候选）：

```json
[
 {"step":1,"label":"2025/26 薪俸税","kind":"context","detail":{"year":"2025_26","tax_type":"salaries_tax","profile_id":"part5_shared_v2","assumptions":[]}},
 {"step":2,"label":"基本免税额132000港元","kind":"decision","detail":{"year":"2025_26","scenario_id":"salary_separate","claim_id":null,"claimant_id":"p1","subject_id":"p1","item_kind":"basic","entitlement":"eligible","grant":"allowed","amount":"132000","reason_codes":["BASIC_ENTITLED"],"rule_refs":["s28"]}},
 {"step":3,"label":"本年薪俸税计算","kind":"assessment","detail":{"year":"2025_26","scenario_id":"salary_separate","unit_id":"salary:p1","branch":"salary_floor_min_v1","actors":["p1"],"bases":[{"name":"nai","value":"240000/1"},{"name":"part4a","value":"9000/1"},{"name":"allowances","value":"132000/1"},{"name":"nci","value":"99000/1"},{"name":"standard_base","value":"231000/1"}],"operations":[{"op":"progressive_tax","inputs":["99000/1"],"output":"3940/1"},{"op":"standard_tax","inputs":["231000/1"],"output":"34650/1"},{"op":"floor","inputs":["3940/1"],"output":"3940/1"},{"op":"floor","inputs":["34650/1"],"output":"34650/1"},{"op":"choose_floor_min","inputs":["3940/1","34650/1"],"output":"3940/1"},{"op":"floor","inputs":["3940/1"],"output":"3940/1"},{"op":"rebate_min","inputs":["3940/1","1/1","3000/1"],"output":"3000/1"},{"op":"floor","inputs":["3000/1"],"output":"3000/1"},{"op":"subtract","inputs":["3940/1","3000/1"],"output":"940/1"}],"amounts":{"before_reduction":"3940","reduction":"3000","final_after_reduction":"940"},"shares":[]}},
 {"step":4,"label":"已保存方案：部分结果","kind":"scenario","detail":{"year":"2025_26","scenario_id":"salary_separate","status":"partial","unit_ids":["salary:p1"],"amounts":{"before_reduction":"3940","reduction":"3000","final_after_reduction":"940","provisional_paid":"0","next_provisional":null,"balance":null}}},
 {"step":5,"label":"所列候选本年比较已确定","kind":"selection","detail":{"year":"2025_26","requested":["salary_separate"],"selected":"salary_separate","best":"salary_separate","reason_codes":["REQUESTED_SET_DETERMINATE"],"candidate_checks":[{"candidate_id":"salary_separate","admission":"eligible","legal_path":null,"responsible_person_id":"p1","separate_final":"940","conditional_final":null,"reason_codes":["SEPARATE_DEFAULT"]}]}},
 {"step":6,"label":"下一年度资料不足，未能估算","kind":"next_year","detail":{"year":"2026_27","status":"unavailable","selected":null,"amount":null,"reason_codes":["TARGET_FACTS_MISSING"],"assumptions":[]}}
]
```

- 记录、网页、打印/下载、AI `read_result` **只读**已保存结果：历史记录不经 v2 校验、不重新 `build_core`、不重算资格/择案/生成说明。**旧 v1 记录的历史网页/打印/下载保持原 canonical core/input/steps/binding 字节不变，legacy label/step 字段按原渲染回退显示、不重建、不补 steps；导出保留原记录全部字段。`steps=[]` 仅适用于 AI `read_result` 对旧无 tag 记录的 selected-trace 视图（§16），不得套用到历史 web/print/download/export。**

## 15. 冻结 profile、金额矩阵与槽位退役（仍不授权 auto slot）

### 15.1 固定语义 profile 与 digest

在现有 `frozen_semantics.profiles` 数组增加下列条目；保存条目恰为 **P** 加 `semantic_digest`。这些字符串是固定实现契约标识，不是可执行 DSL；沿用实际 bundle 的 `profile_id` 拼写，manifest 的 `profile_ID` 字段不改。

```json
{
  "profile_id": "part5_shared_v2",
  "profile_version": 1,
  "engines": ["personal_assessment", "salaries_tax"],
  "input_schema_version": "2.0.0",
  "contracts": {
    "facts": "subject_relation_year_v2",
    "temporal": "coverage_three_value_clause_quantifiers_v2_salary_window",
    "entitlement": "independent_of_claim_intent_v2_ownership_twopass",
    "ledger": "per_scenario_typed_mutex_enbloc_v2_ownership",
    "candidates": "stage1_explicit_actors_units_effective_v2",
    "future": "next_year_reparse_no_fallback_v1",
    "assumptions": ["same_income", "same_mpf"],
    "selection": "unknown_competitor_blocks_best_v1",
    "money": "annex_c_c2_exact_v1",
    "trace": "stage1_tagged_steps_v2_candidate_checks"
  },
  "allowance_groups": {
    "child": ["base", "birth_year", "following_year"],
    "child_cap_mode": "independent_per_group",
    "grandparent_amount_source": "parent",
    "following_year_applicable": ["2026_27"]
  },
  "rounding": {
    "salary": "salary_floor_min_v1",
    "pa_individual": "pa_unit_floor_min_v1",
    "pa_joint": "pa_unit_floor_min_v1",
    "pa_no_pa_salary": "pa_stc_comp_tp_v1",
    "pa_no_pa_property": "property_per_assessment_floor_v1",
    "pa_no_pa_business": "approved_unincorporated_floor_branch_v1"
  },
  "components": {
    "salary_partial_balance": "null",
    "pa_status_ceiling": "partial",
    "pa_balance": "null",
    "payment_comparison": "out_of_scope",
    "rebate_applicable_years": ["2024_25", "2025_26"]
  }
}
```

- `semantic_digest = "sha256:" + SHA256(canonical_json_bytes(P)).hexdigest()`；canonical JSON 完全沿用 C2.7（键排序、UTF-8、无空白分隔符、拒绝重复键）；**不对含自身 digest 的对象再哈希**。
- 不纳入 digest：金额、cap 数值、原文/Markdown 文件哈希、排版、时间戳、bundle/record/session ID、运行报告。金额由既有 `bundle_hash` 覆盖。
- 契约标识含义随获批附件冻结；改变谓词/顺序/候选语义必须修订相应标识及 profile，不得保持同标识而换实现。引擎持有发行版固定 P 和预期 digest；兼容检查同时验证“保存 digest 确实匹配 P”和“P 等于本引擎支持的固定 P”，不是只验证自洽。profile 核验用于 pin/prepare、HTTP/AI 执行、发布/回滚及 replay；历史查看不运行新输入校验。
- **不增加任何 binding/哈希对象字段**：binding 仍恰为四元组；`RuleBundleContent` 仍六字段；`CalculationCore` 仍十一字段；六金额键不变；P/digest 经既有 `frozen_semantics` 被 `bundle_hash` 覆盖。其余五税 profile 原样保留；现有 salary/PA 概括性说明须引用 §14 具体分支，不能再以一句“PA 全部 raw 比较”覆盖。

### 15.2 完整共享金额矩阵（路径从 data 内部起算；manifest 禁止再写 `data.` 前缀）

每个 `allowances.<YA>.<K>` 恰为 `{amount:Money, cap:Money|null, status:"applicable"|"not_applicable_verified"}`。

| K | 2024_25 / 2025_26 amount | 2026_27 amount | cap |
| --- | ---: | ---: | --- |
| basic | 132000 | 145000 | null |
| married | 264000 | 290000 | null |
| child_base | 130000 | 140000 | 1170000 / 1260000 |
| child_birth_year_additional | 130000 | 140000 | 1170000 / 1260000 |
| child_following_year_additional | 0 | 140000 | 0 / 1260000 |
| parent_high、parent_high_cohabitation | 各 50000 | 各 55000 | null |
| parent_low、parent_low_cohabitation | 各 25000 | 各 27500 | null |
| sibling | 37500 | 37500 | null |
| personal_disability、disabled_dependant | 各 75000 | 各 75000 | null |
| single_parent | 132000 | 145000 | null |
| parent_cash_threshold | 12000 | 12000 | null |

- 除前两年度 following-year 节点为 `not_applicable_verified` 外，上表 status 均 `applicable`；null cap 只表示该节点没有此类聚合 cap，不表示未知。三年度节点缺失＝规则不可核验；不得补零。status/分组/适用年度是冻结语义，不是自动金额槽。
- **缺键区分（与 §7 一致）**：矩阵节点缺键＝运行期数据缺失 → `E_RULES_STATE_UNVERIFIABLE`（fail-closed）；附表 4 无条目＝来源侧法定不存在，只经已核转换写为显式 `not_applicable_verified`（计 0＋理由）。不得以“缺数据”推断“法定不适用”，也不得以“法定不适用”掩盖缺键。
- 唯一 MPF 数值为 `allowances.mpf_deduction_cap:"18000"`，有效范围限定三个已支持年度；删除两税私有副本。唯一 salary/PA 宽减表为 `reductions.<YA>.{percent_bp,cap,status}`：2024=`10000,"1500",applicable`；2025=`10000,"3000",applicable`；2026=`0,"0",not_applicable_verified`。profits 宽减不并入该表。既有 ERCE cap 仍留 `salaries.deductions.elder_residential_care.caps.<YA>`，PA 读取同一现有源，不另建副本。

### 15.3 六条旧 salary/PA 路径：本期全部 stop-auto（仅观察）

| 旧路径 | 固定观察值映射 |
| --- | --- |
| salaries.allowances.2025_26.basic | allowances.2025_26.basic.amount |
| salaries.allowances.2025_26.married | allowances.2025_26.married.amount |
| salaries.allowances.2026_27.basic | allowances.2026_27.basic.amount |
| salaries.mpf_deduction_cap | allowances.mpf_deduction_cap |
| salaries.reductions.2024_25.cap | reductions.2024_25.cap |
| salaries.reductions.2025_26.cap | reductions.2025_26.cap |

- 以上六个来源实例移出真实 manifest.slots；**不新增替代 auto slot**。保留原 anchors 及来源登记；原 slots 七字段描述移至 `main.py` 固定内部 `STOP_AUTO_OBSERVATIONS`，不是 manifest 新增字段、也不是新写入白名单。
- 观察流程：对这些描述复用**现有** extract，以临时只读 manifest 副本提供观察 slots；正常发布链只接收去除后的真实 manifest，**禁止把观察副本传给 classifier**。差异与失败一律进入 §15.5 observer pending 状态机（不生成可发布候选；已知失败不得因采样/离线/重启/回滚被清除）。
- MPF 变化影响全部三个支持年度；年度金额变化只标对应年度。观察解析/定位/标量失败＝持久 `unknown`（sticky），不是数值修例结论；同值尾随零经既有 AmountStr 规范化后为 no-op，不误报变化。
- classifier 再以固定停止路径集合拒绝注入的旧路径或新共享路径；这些防线不能仅靠 UI 隐藏。真实 manifest 允许 slots 为空，相应字段转 evidence-only；修订“所有来源 slots 必须非空”的历史装配断言，不补虚假槽位、不扩解析依赖。
- **不受影响、保留原 manifest 配置的路径（8 条）**：`stamps.property.related_conveyance.fixed_duty`、`stamps.stock.other_transfer_fixed`、`stamps.lease.rent_ceiling_unit`、`stamps.stock.effective_from`、`property.years.2025_26.standard_rate`、`property.repair_allowance_ratio`、`profits.two_tier_threshold`、`profits.reductions.2025_26.cap`。“保留配置”不意味着跳过原类型、证据或新增报告核验；现有不支持的 slot 类型不能借本 CR 放行。
- 上述 8 条的受影响 case ID 为**计划 Red 证据**（`a_avd_related_fixed`、`a_stock_other_fixed`、`a_lease_ceiling_boundary`、`a_stock_effective_boundary`、`a_property_rate`、`a_property_repair_ratio`、`a_profits_threshold_boundary`、`a_profits_rebate_cap`），**当前不存在、未验证**；每 case 须给固定输入、独立参数证据、阶段期望及边界分支；缺任一证明 → 该次候选隔离，不用 static case 替代。明细与静态/候选分离见 006 §7。

### 15.4 发布报告绑定与候选验证

- 沿用 reference/testgate/publisher，不另起框架。静态回归运行**发行版完整批准 bundle 副本**，不仅替换一个税区；该副本及固定期望一起登记 baseline hash。真实候选验证把**原样 candidate_content** 交给实际引擎，不覆盖其 data；参考侧只读取独立证据/工作纸，不读取候选参数或生产税务 helper。现有三个 case 保留为 static regression，不能计作任一变化槽位的 affected coverage。
- 报告在现有结构内增加封闭绑定信息：

```text
binding = {baseline_bundle_id,baseline_bundle_hash,candidate_bundle_hash,
           engine_version,rules_schema_version,
           profiles:[{profile_id,semantic_digest}],
           manifests_sha256,evidence_sha256,reference_code_sha256,
           required_case_ids:[string]}
coverage = {changed_paths:[string],affected_case_ids:[string],static_case_ids:[string]}
case row = {case_id,mode:"candidate"|"static",input_proof_sha256,subcase_ids:[string],
            worksheet_sha256,parameter_evidence_refs:[string],stage_match:bool,passed:bool}
```

- **每 candidate case 的 subcase 绑定（B5 澄清）**：既有八行所示历史数值是**基线示例**，不是新参数的固定 witness；对变化的 threshold/cap/ceiling/rate 须由**独立已核参数胶囊**构造 proof-controlled 子 case（禁止 candidate.data/生产 helper），实际跨过新适用阈值/cap/取整边界且至少一个分支/阶段影响可观察；static baseline 不得批准变化参数；缺合法 in-scope witness → `coverage_gap`/quarantine；subcase input/worksheet/阶段期望 hash 登记入 proof 与实际执行收据（与 006 §7.1–7.3 一致）。

- 数组按 ID/path 排序去重；`manifests_sha256` 覆盖完整实际发布 manifest；`evidence_sha256` 覆盖规范独立事实及其来源快照 hash/anchor/tier，不仅覆盖 fact_id 列表。
- 受影响 case 集合由**服务器固定 path→case 表**计算，不采信报告自行缩小的集合；跨路径变更取并集。publisher 重算 candidate hash、profile、manifest/evidence 摘要及 required 集合；核对全部 candidate case 实际执行/通过、static 回归零失败、无网络、无 session、未写 current pointer。
- 发布事务内比较**预期 baseline 指针**，再写 bundle/publication/audit；不匹配或报告被换用 → 零切换。门禁隔离证据须为**实际观察**（临时 store 路径、出站计数、session、pointer 写状态、case 执行身份），`0` 字面量或生产者重算不算证明。报告不接受 HTTP 上传，仍是可信进程内管线产物，不新增签名系统。
- 报告新增顶层 `purpose:"seed"|"update"`（不改税务四元组/core）。`binding.baseline_bundle_id/baseline_bundle_hash` 明确 nullable：update 两者非 null（按实际 current 指针及内容校验后生成）；seed 两者均 null（只允许发行版固定 candidate/hash/profile＋绑定验证产物）；单个 null/未知 purpose/不符组合 → 拒绝。服务器按 purpose 与实际 diff **重算 required 集合**，报告不能自减；seed 使用固定 release-required 集合，不要求为无参数变化制造八个 affected witness。
- publisher 入事务后：update＝current 指针仍等于预期 baseline 且内容 hash/schema 兼容；seed＝`current_pointer` 无行且 `rule_bundles` 零行且无损坏；随后再次匹配报告 candidate/hash/profile/proof，才写 bundle/publication/audit/指针；任何不符零切换；禁止任意 AppError 触发 seed。P1/P2 注入的固定测试 seed 不是生产 bootstrap 批准（P3 才用发行产物）。
- 候选输入纪律：实际引擎接收**完整原样 candidate_content**、不替换任何 data 区；run 前后重算完整 hash；read receipt 只证明读取，**必须另有对应分支与独立阶段结果**；static regression 用完整发行 baseline 并与 affected case 独立列示，**永不补足 affected 缺口**；无 in-scope witness 的路径报告 `coverage_gap` 并隔离，不伪造 passed case。
- 门禁观测（B5.3，计划）：仅覆盖同步受信 case 的**上下文局部**观察作用域（不是 Python 沙箱、不接受下载代码/插件；非 gate 调用委托原实现；**gate 内禁止新线程/子进程，防止 ContextVar 观测上下文逃逸**；只引用 006 §7.2 既有机制，不新建框架/端点）。之前建立唯一临时目录并校验 SQLite 路径、登记 DB、读取 pointer/publications 基线；期间计数并拒绝网络/socket/DNS 尝试（含绕过 outbound 直连 HTTPX）与 SessionRegistry 会话创建，观察 SQL 写入（含写后恢复）；之后与 finally 重读全部实际 DB、核对写尝试日志、candidate hash、case 执行身份与包装复原；counter 来自观察器，缺失/异常不得填零通过。明细与负例见 006 §7.2。
- 新空库 seed 只接受发行版固定 bundle 及其绑定验证产物；不新增 admin 迁移入口。
- **存储五态冻结表（恢复前稿已被接受的判定，判定性、非任意 AppError）**：

| 状态 | 判定 | 处置 |
| --- | --- | --- |
| 真空库 | `current_pointer` 无行 **且** `rule_bundles` 零行 | 允许发布随发行固定 v2 seed（仅此一况；配合 `HKTAX_DB_PATH` 显式新空库为批准选择） |
| 孤儿束 | 无指针行但有束行 | fail-closed，**不得当空库 reseed** |
| 悬空指针 | 指针行引用的 bundle 不存在 | fail-closed |
| 内容/哈希损坏 | 载入内容与 `bundle_hash` 不符 | fail-closed |
| 不兼容旧库 | `rules_schema_version != 2.0.0`，或 profile 预期 P/digest 不符，或所需 allowance 节点缺失 | fail-closed 阻新计算；旧记录查看/导出不受影响 |

- **五个守卫入口同一守卫并核验预期 profile**：pin/prepare（含兼容与可用性）、HTTP execute、AI `calculate_confirmed`、publish/rollback、strict replay——均核验“保存 digest 匹配 P”且“P 等于本引擎支持的固定 P”；引擎版本不完全相等 → 全部 7 个旧执行目标 replay 拒绝（409 `E_BUNDLE_INCOMPATIBLE`），原查看/导出/打印不动。不得借 stop-auto 变相加入 admin 迁移入口。

### 15.5 Observer pending 状态机与 Resolution（B4＋补充；不改 DDL）

- 存储复用既有表：`source_id`＝原逻辑 source_id；内部 `state="observer_pending:"+映射后的 data-relative 路径`（仅既定六条 stop-auto 映射；任意其他 suffix 不得进入）；`period`＝受影响 YA；详情存既有 `updater_meta` key `observer_pending/{source_id}/{period}/{path}`。
- 详情：`{source_id,path,period,sticky:bool,first_observation:Event|null,latest_observation:Event,resolution:Resolution|null}`；`Event={sequence:int,baseline_bundle_id:string|null,baseline_bundle_hash:hash|null,kind,detail,event_digest}`（sequence 按 owner/path/period 单调；event_digest 哈希 Event 除自身字段的 canonical JSON 并绑定 baseline 身份；**baseline 对：非 null 成对（已核 baseline 必填）；仅当确无已核 baseline/真空上下文时允许两者同为 null；禁止单 null 或默认零值**）；kind/detail 为封闭联合：`checking{phase:"manifest"|"parse",snapshot_hash:hash|null}`、`manifest_ok{}`、`value_equal{value:canonicalMoney,snapshot_hash}`、`value_changed{baseline_value,observed_value,snapshot_hash}`、`observation_failed{phase:"manifest"|"parse"|"normalize"|"interrupted",code:"invalid_manifest"|"anchor_missing"|"parse_error"|"invalid_scalar"|"interrupted_check",snapshot_hash:hash|null}`、`transport_unavailable{code:"unreachable"|"fetch_error",last_verified_snapshot_hash:hash|null}`；禁止原异常全文。
- 事件规则：**sticky=true 时 `first_observation` 必非 null、`latest.sequence ≥ first.sequence`**；first 原因（`unknown`/`related_change_uncertain`）不得被后续 value_equal/offline 降级；临时 `checking` 仅在其**匹配 owner/path/period＋sequence** 的正常结束可移除（`interrupted_check` 转 sticky `unknown`）；Resolution 的 `expected_current_bundle_id/hash` 为显式 nullable 对（仅真正空库/无基线上下文可同 null，禁单 null）；first/latest/current/proof 匹配要求不变。
- 公开映射：`source_states()`/availability 把这些内部行映射为**既有** `unknown`/`related_change_uncertain`/`trusted_offline`（不新增对外枚举）；详情缺失/损坏、路径不在固定集合、详情与状态键不符 → 该期间 unknown 阻断，不能当不存在。
- 状态转换：值确实不同 → sticky `related_change_uncertain`；manifest/解析/定位/标量失败 → sticky `unknown`；已有 sticky 后 value_equal/manifest_ok/再次成功/离线/重启/回滚 → **保留 sticky 与 first**、只更新 latest，不自动清除；无旧 sticky 的单纯网络不可达且最近已核快照有效 → 既有 C7 trusted_offline；无可用已核快照的网络失败 → 不允许放行；重启发现未完成 checking → 继续 unknown＋`interrupted_check`。
- 防漏 guard：**manifest 校验前**先提交该 source 观察路径的 durable checking；fetch 成功后 **extract/normalize 前**再提交 parse checking（携带 snapshot hash）；若 checking 提交失败则禁止开始该阶段；checking 公开为 unknown，仅本次正常结束且从无 sticky 时可移除本次临时 marker（不是清除历史 pending）；每 source 独立完成观察与落盘后再进入全局 any_failed，另一来源失败不得跳过 guard 提交。
- 标量：六条 observer 均对 extract 文本执行既有 AmountStr 校验/canonical 化，再检查原 slot 值域/步长；baseline 同样规范化；`"18000.00"`≡`"18000"`；禁止 float 比较，不新增去逗号/货币前缀/HTML/PDF 解析。
- Resolution（唯一清除）：`{disposition:"no_effect"|"approved_bundle", pending_origin_digest, latest_event_digest, expected_current_bundle_id/hash(nullable 对，仅真空/无基线上下文同 null), proof_refs, proof_sha256, target_bundle_hash:null|string}`；清除事务须同时匹配 first sticky 原因、**实际最新事件**（含失败/离线事件）、当前指针及独立 proof（approved_bundle 另须匹配批准目标 hash）；普通成功抓取/同值不是 Resolution；详情缺失/损坏不能清除；MPF 三个年度分别 pending，不能只清一个；无本阶段授权入口则保持 pending（不加 admin/自动清除）。
- 事务与读取：`_sync_source_states` 只管理既有普通 C7 状态，其 SELECT/delete 集合排除全部 `observer_pending:` 行；每 source 本轮 notice 同步与 observer 差异落盘在同一 store 写事务；availability 读提交后快照，HTTP/AI 消费临界区再核对；**不存在“先删除再重新插入”的放行窗口**。

## 16. AI 工具信封与本地返回

- `propose_input` / `validate_input` 仍只收一个参数：`{"input":{"tax_type":…,"schema_version":…,"input":…}}`；内部 input 是**完整 HTTP 业务信封**（不是裸 facts），不从聊天文本或 ToolContext 猜税项。映射固定：`salaries_tax`、`personal_assessment` → `2.0.0`；`profits_tax`、`property_tax`、`stamp_property`、`stamp_stock`、`stamp_lease` → `1.0.0`（全 7 映射）。
- ToolContext 增加只读 precheck context：pinned bundle/四元组、current/next AvailabilityContext、调用开始 epoch/revision；执行与 HTTP 相同校验器＋语义预检；不调用 prepare/invalidate、不生成能力、不补默认资格。
- `calculate_confirmed` 与 HTTP 共用 §12.3 的 consume/complete/fail 语义与 ExecutionContext 快照；响应可附运行时 `execution` 元数据，但不写入十一 core/四 binding；已消费/在途外发边界不变。
- 工具条目始终为七键 `{id,name,outcome,note,needs_confirmation,error,result}`：
  - 成功：`outcome="executed"`、`note="read_only_checked"`、`needs_confirmation=false`、`error=null`；`result` 恰为 `{proposal:{tax_type,schema_version,input:canonical},canonical_input_hash,expected_binding,status:"checked",questions:[],missing_components:[]}`；未来缺项列 missing_components，不阻止已知本年提案。
  - 拒绝：`outcome="rejected"`、`note=null`、`needs_confirmation=false`、`result=null`；`error` 恰为 `{code,message_zh,field,details,binding,status,questions,blocked_reason}`，无值者 null/空数组；沿用 AppError 分类，不回显原始输入或上游响应。
  - 未知工具继续既有 ignored 行为；七键不变；不新增 confirm/settings/update 工具。hash 仍为 canonical 业务 input 的 hash；schema/tax/target 由服务端确认记录分别绑定；checked 不是确认授权。
- 聊天“采用”仅把 proposal 填入表单→invalidate→重新 HTTP prepare→人工 confirm；**不自动上传 proposal/facts，不自动重发模型请求**。
- `read_result({record_id})` 本地 result 恰为：`{record_id,tax_type,binding,status,amounts,blocked_reason,missing_components,unsupported,pending_verification,steps}`。steps 仅从保存 core 选 `scenario/selection/next_year` 三类原条目；不读取 raw input，不返回 decision 的伤残/关系事实，不重算税额或生成新资格说明；旧无 tag 记录在 **AI read_result** 返回 steps=[]（仅此视图；历史网页/打印/下载按 §14 保留原字节），不重建。
- 本地返回**不等于**模型外发白名单：默认外发仍只有已预览授权的既有字段；record 结果不得自动追加成 tool 消息。若用户另选发送结果摘要，先显式选取必要 status/amounts 及原因，再最小化、冻结字节、新预览、**新 consent**；不发送完整 core/input/完整 trace 或能力 ID。本 CR 不新增任何隐私/信任边界能力。

## 17. 与 UI 的功能接口要求（功能清单；视觉/交互由 designer 004 保留）

1. 薪俸表单实际入口为首页 `/`（`salaries.html` 仅说明页）；首页 collector 纳入 v2 字段映射（年份限定 locator，避免目标年/下一年碰撞）。
2. 首页全链真实接线：**单一 mutation 队列覆盖 invalidate 与 prepare**（同标签页仅一个在途；在途编辑合并 generation；ACK 后按最新草稿继续；迟到响应丢弃）→ prepare→缺项定位（questions→错误摘要→展开聚焦）→confirm 弹窗→execute→结果渲染（null≠0、四态徽标）→记录查看；编辑立即禁用旧 confirm/execute/send（§12）。**同一 ACTIVE operation＋同一 live epoch 的 Operation ACK 推进内部已确认版本（即使本地 generation 已变），旧 epoch/已取消/重同步后 ACK 忽略，Current 头不自动接纳**（与 004 §7/flows 18/19/21 一致）。
3. 聊天：结构化提案审阅/采用路径（§16）；采用填表→invalidate→重新 prepare→人工确认；不靠回显宣称可计算；相关 salary 实际 PA 选择事实需控件，但**不新增完整 PA 工作台**（个人入息课税是评税选择；本阶段仅有 API，网页不提供计算工作台；只比较列明的有限方案，结果为部分可算，不代表完整最优或缴款比较）。
4. 覆盖矩阵（`app/web/coverage_data.py::coverage_items()` 现仅 5 条 unimplemented）逐普通类别/阶段更新为 `support_level ∈ {computable, needs_input, manual_handoff}`＋`phase`＋`limits_zh`；阶段 2/3 项目不得称已支持或“完整最优”。**新增纯内部 helper `chat_coverage_guidance(items)`（同文件；只按既有 `support_level/phase/limits_zh/tax/subtopic/applicable_periods` 有限映射，不读用户 facts/消息/模型输出）**；`index_page` 调 `coverage_items()` **一次**并把本地视图传入聊天面板；API/覆盖页/聊天共用同一数据源（不维护聊天专用清单、不做 heuristic NLP、不新增端点/工具/外发键、不 auto preview/grant/send/retry/history append/facts upload/冻结字节后注入）。
5. 无新布局/样式决策；任何视觉/交互细节以 designer 004 与功能字段对账为准，不把关键入口留到 Green 临场决定。
6. 004 的七个计划浏览器场景（singleflight 快速编辑/延迟 ACK、prepare 在途编辑 ACK 重排、跨标签页 409 显式重同步、clear epoch 丢弃迟到响应、actual/hypothetical salary nomination 与条件式文案、child DDA 提名≠spouse DDA、聚焦/consent/零自动外发）登记于 006 §6.7；nomination 控件为每年 actual/hypothetical 独立组，与 child nominee 控件不同；s.10(a) 责任派生、s.10(b) 显式提名无 self/高收入默认；不改变视觉/布局。
7. 聊天本地覆盖指引的确定性标签（D 合同）：phase1＋computable→“列明范围可计算；仍须规则可用、资料及资格确认”；needs_input→“缺资料待确认；未知不是否或零”；manual_handoff→“需人工核实／本阶段范围外”＋显示 `limits_zh`；phase2/3→“后续阶段，当前不支持计算”且不生成计算入口；PA 条目→协调者更正文案（评税选择；本阶段仅有 API，网页不提供计算工作台；有限方案、部分可算、不代表完整最优/缴款比较）；未识别/缺失→“支持范围尚未确认；不作可计算承诺”＋链接 `/coverage`。本地入口标“服务器支持范围说明，非模型回答”（未配置/离线可用）；模型自由文本仍 `unconfirmed`、**非权威**，不能据其产生官方覆盖徽标、计算入口或能力授权；“never claims”类断言只检验**权威本地 UI/控制**，不承诺全模型输出真值过滤（后者另属 `NEEDS_USER_DECISION`，本 CR 不做）。具体展开/按钮/焦点由 designer 决定。
