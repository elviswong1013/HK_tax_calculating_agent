# 002 — 香港税务计算 Agent：技术契约（Technical Contract Annex）

Status: Approved（随 specs/001 用户 Gate2 2026-10-08 批准）
Created: 2026-10-07
Updated: 2026-10-08
父规格: specs/001-hk-tax-agent.md（SDD，Draft；2026-10-08 第二次复审 NEEDS REVISION，整改待复审）
评审依据: docs/reviews/2026-10-07-sdd-plan-review.md（@plan-reviewer，2026-10-07，首轮全量）；docs/reviews/2026-10-07-technical-pre-review.md（@plan-reviewer 技术预审，2026-10-07，NEEDS REVISION：仅 R11 文档级闭合，R6–R10/R12–R14 OPEN；预审≠全局审批）；**@plan-reviewer 第二次复审（2026-10-08）：NEEDS REVISION——R10/R11/R13 文档级闭合，其余技术/契约项本批整改，待再次复审**（复审意见由编排者转达）
范围边界: 本文只承接 **R6–R14 技术契约**；R1–R5 税法语义（两径/公式/资格/分配取整/AVD 过渡）**不在本文选择**，待并行研究返回后另行整合。本文不授权任何生产代码或测试。

> 条款效力：C1–C8 为规范性条款；与 SDD 正文冲突时以更严格者为准。C9 给出条款→REQ 映射，C10 给出新增/替换命名测试（计划，未创建），C11 给出统一 Gate2 前置。本文不声称任何版本已经过验证或任何测试已运行；技术预审不构成全局审批，复审前本文全部整改均为「待复核」。

## C1 平台与依赖基线（R14 → REQ-16）

| 项 | 契约 |
| --- | --- |
| Python | `>=3.12,<3.14`（**获批解释器仅 Python 3.12/3.13**）；Windows 10/11 为基线运行环境。**本机默认解释器 3.14.5 超出基线、禁用**；创建 venv 必须显式选择获批版本（命令模板 `py -3.13 -m venv .venv`，或显式解释器路径占位），创建后执行版本检查（`.venv\Scripts\python --version` 必须输出 3.12.x/3.13.x，否则删除重建）；本契约不执行任何安装 |
| 时区 | `zoneinfo` + 显式 `tzdata` 依赖，保证 Windows 上 IANA `Asia/Hong_Kong` 可用；不可用即启动失败 |
| 运行时依赖 | `fastapi >=0.115,<1`；`pydantic >=2.10,<3`；`uvicorn >=0.30,<1`；`jinja2 >=3.1,<4`；`httpx >=0.27,<1` |
| 开发依赖 | `pytest >=8,<10`；`hypothesis >=6,<7`；`pytest-asyncio >=0.24,<2`；`pytest-playwright >=0.5,<1` + Chromium |
| 测试接线 | API/单元测试经 `httpx.ASGITransport` 进程内执行；API context 以 `TestClient` 生命周期（`with TestClient(app)`）或显式 ASGI lifespan 固定启动/关闭；假件（假时钟、假 outbound transport、临时 DB、合法 Host 表）为进程内实现，**不新增第三方库**（不引 freezegun/responses 等）；单元层不启动浏览器；仅 e2e 用 Playwright |
| 解析依赖（**OPEN gate；用户已答复**） | 用户于 **2026-10-07 明确答复「暂不增加依赖」**：**不批准** `beautifulsoup4`（HTML DOM 解析）/`pypdf`（PDF 版式读取），**不增加、不安装**任何解析依赖；解析选型保持 OPEN gate——用户改变决定前不引入任何解析方案，**不得以其他方案变相绕过用户决定**；其余文档工作先行，R9 解析实现路径保持未决（见 C7.3）；版本号与许可仍未核实，不得写成已核实 |
| 固化时机 | Gate2 ＋ 用户明确授权依赖安装后：①先建 **test-only** `pyproject.toml`＋`[dev]` extras＋虚拟环境 → ②第一 Red；③**Green** 阶段才加入 production 运行时依赖并实测锁定版本与许可清单。本文只定区间，不声称今日已验证任何具体版本 |

## C2 金额与标量语法（R7 → REQ-9/10）

- **C2.1 AmountStr**：ASCII **全匹配** `^-?(0|[1-9][0-9]{0,29})(\.[0-9]{1,2})?$`（整数部分 1–30 位＝最多 30 位整数；原 `{0,30}` 实际允许 31 位，已修正）；前导 `-` 仅允许出现在显式亏损/退款字段；无 `+`。禁止：JSON number、boolean、指数、空白、千位符、前导零；长度 ≤32 字符；绝对值 ≤1e14；小数 ≤2 位；报税表字段可声明为整数港币（0 位小数）。
- **C2.2 规范化**：接受尾随小数零并规范化为无尾零形式；规范形不含正号；`-0`（负零）拒绝。
- **C2.3 RateStr / RatioStr / Shares**：RateStr 为 ASCII 无符号十进制 ∈ [0,1]，≤8 位小数，≤32 字符。RatioStr = `num/den`：两侧均为 ASCII 正整数 ∈ [1,1e9]，以 gcd 约分为规范形（`1/3` 合法；`2/6` 规范化为 `1/3`）；全程禁浮点。Shares 列表满足 `Σ share == 1`（精确有理），否则 `E_INPUT_CONTRADICTORY`。
- **C2.4 精度**：Decimal 上下文 `precision=50` 仅用于有限运算；份额/分配等非十进制终止中间值一律以**整数有理**（Fraction，源自 Decimal 字符串的精确缩放）计算，禁用二进制浮点；仅在规格指定的税务取整点转换：以**整数商/余数**精确完成取整（round 于整数商余层面），再转 Decimal 输出。用户侧 2 位小数上限只约束输入/输出语法，**不限制**内部步骤精度（steps 内保持精确有理）。**分配取整的法律规则不在本文选择（R4 未决）**；通用分配必须同时具备冻结策略字段与精确比率输入，缺一即 `blocked`。
- **C2.5 输入输出形态**：金额 I/O 均为规范 Decimal 字符串；步骤记录在非终止时携带精确有理表示（`num/den`）；界面近似值必须标注「非 canonical 税额」。
- **C2.6 Pydantic BEFORE 校验器**：先严格 str 判定，再正则全匹配，再 `Decimal(value)` 构造；任何 int/float/bool → Decimal 的隐式强制视为实现缺陷。
- **C2.7 Canonical JSON**：键排序、UTF-8、无空白分隔符；解析期**拒绝重复键**。
- **C2.8 哈希对象冻结（封闭字段清单）**：哈希输入＝**封闭命名字段集合**的 canonical JSON（C2.7：键排序、UTF-8、无空白分隔符、拒绝重复键）；实现者**不得增删字段、不得临场决定**纳入或排除——凡未列入清单的字段按构造在哈希对象之外，**不存在开放式排除清单**。序列化边界＝该封闭对象本身，外层信封键一律不进入序列化。
  - **`RuleBundleContent`【拟名】**（`bundle_hash` 的唯一哈希对象）**恰含**以下字段：①`rules_schema_version`（冻结 schema 版本）；②`effective`（法律生效时刻/日期；以税法 annex 核实的**条款**为准，**不得以条例 metadata 的 commencement 时刻推断文书签署时分门控**）；③`applicability`（适用期间/期间键）；④`frozen_semantics`（公式/取整/资格轮廓等冻结语义元数据，含各 profile 的 `profile_ID`＋`semantic_digest`）；⑤`data`（各税种 DATA-only 字段）；⑥`evidence_digests`（每条证据的**内容 hash** 清单）。bundle_id、bundle_hash 自身、发布运行时 id、抓取/发布时间戳、快照存储字段等运行时与发布字段**均不在该对象内**。
  - **`CalculationCore`【拟名】**（核心计算记录 hash 的唯一哈希对象）**恰含**以下字段：①`tax_type`；②`input`（canonical 规范确认事实）；③`binding`（完整四元组 `{bundle_id, bundle_hash, rules_schema_version, engine_version}`）；④`status`；⑤`amounts`（六个规范金额键，C3.4）；⑥`blocked_reason`；⑦`steps`（含精确有理中间值，C2.5）；⑧`evidence_refs`；⑨`unsupported`；⑩`pending_verification`；⑪`missing_components`。**运行时信封（request_id、record_id、job id、墙钟时间戳、warnings、questions、分页、health/网络字段等）完全在哈希对象之外**——不参与 core hash，不属于 core，不参与 core 确定性判定，也不承诺整个运行时信封字节一致。
  - **core 确定性**＝同（输入, bundle, 引擎核心）→ 同 `CalculationCore` 序列化字节。
- **C2.9 载荷上限**：请求体 ≤1MB；全局数组 ≤1000 项（资源上限）；**合格子女申索 ≤9**（法定上限；受养父母/兄弟姊妹等非子女受养人不适用 9 上限，仅受全局资源上限约束，其事实与资格范围由 R1 税法 annex 决定）；子女出生日期→申索名次（rank）由服务器按已核规则派生（P0，待税法 annex）——**rank 不是权威输入**：输入 schema 不含 rank，客户端提交 rank 字段按 `E_INPUT_EXTRA` 拒绝，名次一律以服务器派生值为准。
- **C2.10 规范化算法**（实现须逐步一致）：① 全匹配 ASCII 语法；② 去尾随小数零（`1200000.00`→`1200000`；`240000.50`→`240000.5`）；③ 拒绝负零一切形态（`-0`、`-0.00`）与 `+` 前缀；④ 规范字符串由 Decimal（sign/digits/exponent）元组经 **ASCII 普通定点格式化器**生成——禁止以 `str(Decimal)` 充当规范形（locale/指数假设）；**保存/落盘值一律定点、禁指数记法（e/E）**，尾零归零、无前导零；序列化输出前再校验一次无 e/E。
- **C2.11 日期/时刻语法**：wire 日期必须 `YYYY-MM-DD` 全匹配后才做日历校验（不接受时间戳/紧凑日期强制转换）；课税年度键 `YYYY_YY`（`2025_26`＝2025/26，下一年一致）经动态已核期间解析器解析；服务器时刻以 UTC RFC3339 存储并换算 `Asia/Hong_Kong` 供调度。

C2 判定示例：

| 输入 | 判定 |
| --- | --- |
| `"240000"`、`"240000.5"`、`"1200000.00"` | 合法；canonical 化为 `240000`／`240000.5`／`1200000` |
| `"0240000"`、`"2.4e5"`、`" 240000"`、`240000`（JSON number）、`true` | 全部拒绝 |
| `"-0"`、`"-0.00"` | 拒绝（负零一切形态） |
| `"-940"`（仅限显式退款/亏损字段） | 合法 |
| share `"1/3"`＋`"1/3"`＋`"1/3"` | 合法（精确和 = 1） |
| share `"0.33333333"`×3 | 拒绝（share 必须为 RatioStr 且精确和为 1） |

## C3 API、状态与确认（R8 → REQ-2/8/11/13）

### C3.1 端点与方法（权威表）

| 端点 | 方法 | 语义 |
| --- | --- | --- |
| `/api/v1/calc/prepare` | POST | 仅校验事实 → 201 **prepared**（`prepared_id`＋预览/哈希；**非可执行 id**） |
| `/api/v1/calc/confirm` | POST | 用户「确认资料并计算」一键处理器（**UI/CSRF/session 独占**）→ 可执行确认能力 |
| `/api/v1/calc/salaries-tax`／`profits-tax`／`property-tax` | POST | 凭**已确认** `confirmation_id` 原子执行（consumed 重复 → 409） |
| `/api/v1/personal-assessment/compare` | POST | 凭已确认 `confirmation_id`；各合法方案并列 |
| `/api/v1/calc/stamp-duty/property`／`stock`／`lease` | POST | 凭已确认 `confirmation_id` |
| `/api/v1/meta/coverage`／`rules`／`version` | GET | 只读元数据 |
| `/api/v1/update/status` | GET | 徽标数据（尝试/成功/下次/版本/逐源） |
| `/api/v1/update/check` | POST | 触发检查；进行中 → **202 + 现有 job_id**（幂等） |
| `/api/v1/update/jobs/{job_id}` | GET | 作业详情 |
| `/api/v1/update/rollback` | POST | 审计化指针回滚 |
| `/api/v1/ai/consent/preview`／`/api/v1/ai/consent` | POST | 外发预览／授予 |
| `/api/v1/ai/consent/{consent_id}` | DELETE | 撤回（仅阻断未来调用） |
| `/api/v1/ai/chat` | POST | 须有效 consent |
| `/api/v1/records/{record_id}` | GET | 读取记录 |
| `/api/v1/records/{record_id}/download` | GET | **全量规范确认事实**下载（非摘要；no-store；`Content-Disposition: attachment`；排除未使用敏感字段） |
| `/api/v1/records/replay` | POST | 历史记录按**确认事实＋原 binding 校验**后由引擎核心**重算**（引擎版本**完全相等**才兼容；不完全相等 → **409 `E_BUNDLE_INCOMPATIBLE` 拒绝重算：不降级、不迁移**）；不信任导出税额字段 |
| `/api/v1/report/print` | POST | 打印报告（`Content-Disposition: inline`；no-store） |
| `/api/v1/session` | DELETE | 清空会话；两 epoch 均 ++；**删除会话内存 records**，挂起工作与确认/同意能力失效；已下载/打印外部副本不受影响（触发矩阵见 C3.2.4） |

### C3.2 确认三段（prepare → confirm → execute）

- **C3.2.1 prepare**：`POST /api/v1/calc/prepare` 仅做**事实校验**；201 返回 `{prepared_id, input_revision, input_hash(规范), expected_binding, preview, expires_in=300, status:"prepared"}`，其中 `expected_binding` 为**完整四元组** `{bundle_id, bundle_hash, rules_schema_version, engine_version}`——prepared_id **不是可执行 id**。`needs_input`／`blocked` → 422 权威状态＋`questions[]`／`blocked_reason`，**不产生任何 id**。
- **C3.2.2 confirm**：`POST /api/v1/calc/confirm` 是用户「确认资料并计算」一键的处理器（UI/CSRF/session **独占**；LLM 工具不得注册或调用）；请求 `{prepared_id, canonical_input_hash, expected_binding, acknowledge:true}`；成功 → 可执行确认能力 `{confirmation_id(256-bit 随机), expires_in=300}`，绑定 `(session_epoch, input_revision, input_hash, tax_type, schema_version, 完整 binding{bundle_id, bundle_hash, rules_schema_version, engine_version}, execute_target)`；`execute_target`＝发起 prepare 的唯一可执行端点，**错目标执行 → 409 且不消费确认**。即 AC-11 现有运行时一键（确认→计算），**无新增人工门**。
- **C3.2.3 execute**：税项端点原子消费已确认能力；端点必须与 `execute_target` 一致，错目标 → **409 `E_CONFIRMATION_TARGET_MISMATCH` 且不消费**；consumed 重复 → **409 `E_CONFIRMATION_CONSUMED`**（无双重计算），同 session 已有结果时响应附 `record_id`，原结果经其继续可读。
- 输入任何变更 → prepare 与 confirmed 同时失效。`confirmation_id` 对模型负载隐藏（工具脱敏）。
- **C3.2.4 状态边界与历史契约（按触发精确划分，各行互斥适用；失效机制即 C3.2.2 confirm 绑定与 C4.6 consent 绑定中的对应字段变化，不另有隐藏失效）**：

| 触发 | 失效/删除 | 保留 |
| --- | --- | --- |
| `input_revision` 变更（用户编辑输入） | 当前 editor 草稿、`prepared`、**未消费** confirm/consent 能力、迟到 response | 已完成 session record 按**原 binding** 查/下载；按**原 snapshot** 重算（replay 条件见 C3.1 replay 行） |
| rules 变化（bundle/engine 变化、发布/回滚） | 同上：当前 editor 草稿／`prepared`／未消费 confirm 能力、迟到 response，**另使未消费 consent 全部 stale（`E_CONSENT_STALE`；失效机制＝confirm 绑定与 C4.6 consent 绑定的规则四元组 `{bundle_id, bundle_hash, rules_schema_version, engine_version}` 任一字段变化——`bundle_hash` 不含 engine_version（C2.8），纯 engine_version 变化同样触发，不得以同 hash 漏失效）** | 同上；历史回放 ≠ 当前重评 |
| `consent_epoch` 变更（AI 撤回/模型配置变更） | **仅** AI：外发授权、挂起 AI 轮、迟到 AI response | 本地表单、确认能力、本地计算与 record 全部保留 |
| `session_epoch` 变更（清空/会话过期） | **删除**会话内存 records、全部挂起工作与确认/同意能力 | 已下载/已打印的**外部副本**（服务端不持有、不追溯删除） |

已完成 record 的查/下载/重放以**会话存续**为前提（内存态；A3 无财务持久化）；重算不信任导出税额（C3.1），也不删除已导出/打印的外部副本（与 SDD §3 一致）。

请求/响应草图【拟名】：

```json
POST /api/v1/calc/prepare
{ "tax_type": "salaries_tax", "schema_version": "1.0.0", "input": { … } }
→ 201 { "prepared_id": "p-…", "input_revision": 3, "input_hash": "sha256:…",
        "expected_binding": { "bundle_id": "…", "bundle_hash": "sha256:…",
                              "rules_schema_version": "1.0.0", "engine_version": "0.1.0" },
        "preview": {…}, "expires_in": 300, "status": "prepared" }

POST /api/v1/calc/confirm          // UI/CSRF/session 处理器独占
{ "prepared_id": "p-…", "canonical_input_hash": "sha256:…",
  "expected_binding": {…}, "acknowledge": true }
→ 200 { "confirmation_id": "c-…(256-bit)", "expires_in": 300 }

POST /api/v1/calc/salaries-tax
{ "confirmation_id": "c-…" }
→ 200 { "record_id": "…", "status": "complete", … }   // 重复消费 → 409
```

确认状态转移：

| 事件 | 转移 |
| --- | --- |
| prepare 事实校验通过 | `prepared`（无可执行 id） |
| prepare 判定 needs_input / blocked | 422＋questions/blocked_reason，**无 id** |
| confirm（UI 独占；匹配 prepared 且未过期） | `confirmed`（可执行能力，TTL 300s） |
| execute 消费 confirmed | `consumed`（原子；重复 → 409，无双重计算） |
| 错目标端点执行尝试 | **不消费**；409 E_CONFIRMATION_TARGET_MISMATCH；confirmed 状态不变（其他失效事件照常适用） |
| TTL 到期 / 输入变更 / session_epoch 或 bundle、engine 变化 | `expired`／`stale`（终态；重新 prepare） |

### C3.3 权威状态

`status ∈ {complete, partial, needs_input, blocked}`，**唯一权威**；`partial` 布尔仅为派生字段（UI 兼容）。`needs_input` 附结构化中文问题清单。`blocked`＝**不输出任何金额（不是税额 0）**：`amounts` 六个金额键（C3.4）一律**显式 `null`、键不省略**（唯一形态；无「缺省」形态、不填 0），附 `blocked_reason` 与统一错误形状（C3.7）；区别于 `partial` 的部分已知金额。

### C3.4 金额分项（规范字段名）

`before_reduction`、`reduction`、`final_after_reduction`、`provisional_paid`、`next_provisional`、`balance`——各为 canonical 字符串或 null；**balance 符号：正＝欠款，负＝估算退税**；分项为权威输出，禁止客户端以重复相减重建。partial 仅含独立已知组件；缺组件 → 无 `balance`；`blocked` → `amounts` **六个键一律显式 `null`、键不省略**（唯一形态；无「缺省」形态，C3.3）。

### C3.5 类型化输入（判别联合，字段名为契约）

| 族 | 关键字段（节选） |
| --- | --- |
| 公共 | `tax_type`、`schema_version`；`extra="forbid"` |
| 薪俸税 | `year_of_assessment`、`employment_income`、`mpf_mandatory_contributions`、`married_status{value}`、`dependent_children[]{birth_date,residence,claim_owner}`（**rank＝服务器派生，非输入**，见 C2.9）、`dependent_parents[]{relationship,birth_date,residence,claim_owner}`、`deduction_items[]{item_type,amount,eligibility}`（逐项枚举，无 catch-all） |
| 利得税 | **唯一规范模型**（Annex D 由其 lane 照此同步）：`entity_kind ∈ {corporation, partnership, sole_proprietorship}`、`assessable_profit`（确认事实）；partnership 另带 `partners[]{partner_id, partner_kind ∈ {corporation, individual}, share:RatioStr（利润分享比例）}`；`two_tier{connected_entities[]{entity_id, basis_period_end（基期末）, control_basis（控制理由：>50% 股本/表决权/资本或利润，直接或间接）}, no_other_election_same_year（三态）, election_made（三态）}`；`loss_brought_forward`、`deduction_items[]` |
| 物业税 | `rent_received`、`rates_paid_by_owner`、`irrecoverable_rent`、`deposit_offsets`、`ownership_shares[]{owner_id,share}`、`mortgage_interest`（仅允许显式标记「不可扣除」；作为扣除提交即拒） |
| 个人入息课税 | 各税项已核收入/NAV 事实、`property_interest_paid[]{property_id,amount}`、`business_shares[]`、`spouse{facts}`、`options_to_compare[]` |
| 印花税 | `document_type`、`instrument_date`＋**按文书类型的领域字段以 Annex D 为权威**（`docs/design/003-hk-tax-legal-contract.md`，Draft，legalwriter lane 独占）：property＝**两金额**（对价/价值）＋ **max 关系 predicate**；stock＝**每票据 inventory**（逐票据明细）；lease＝**真实 term／rent／premium／duplicate**。本文不定义其语义、**不自行立法**；`instrument_time?` 仅当 Annex D/税法 annex 证实**条款级**时刻门控时才必填（metadata commencement 时刻不构成门控证据）；按金字段不在计租输入中 |

法律判据字段（资格细则、互斥矩阵）由税法 annex（R1–R5）与 **Annex D 领域模型**（`docs/design/003-hk-tax-legal-contract.md`，Draft，legalwriter lane 独占）补充；本文只固定字段名与形态，**不自行立法**。

### C3.6 模型工具注册表与响应双层契约

仅限：`propose_input`、`validate_input`（只读；**不产生任何确认/授权权威**）、`calculate_confirmed`（仅能引用服务器**已存在**的确认能力，无伪造用户标志）、`read_result`；**无** settings/update/rollback 工具；`confirmation_id` 与 consent id 对工具负载脱敏。模型响应契约分**两层**定义（精简现有注册表，**不新增任何工具/功能**）：

- **上游 wire profile（不可信传输层，仅结构校验）**：响应必须先按 OpenAI 兼容 Chat Completions 形状校验——存在 `choices[].message`；`message.content` **可为 `null`**；assistant 消息可携带 `tool_calls[].{id, type, function}`；`function.arguments` 在 wire 层为 **JSON 字符串**（未解析、未做语义校验）；`tool_call_id` 属 **role=`tool` 的回复消息**（与所回复 assistant 消息 `tool_calls[].id` 一一对应），assistant 消息本身仅携带 `tool_calls`（含 id）。**内部规范化 schema 不得直接用于上游（模型端点）请求构造或验证**——外发一律按 C4.6 冻结字节；wire 形状 schema 仅作响应结构校验，不得当作业务/工具入参 schema 使用；wire 结构校验失败 → `E_MODEL_BAD_RESPONSE`。
- **内部规范消息（internal canonical message；wire 规范化后严格校验）**：形状 `{content?: string, tool_calls?: [{id, name, arguments}]}`——`tool_calls[].id` **保留 wire 层调用 id**（内部规范化消息与上游调用一一关联，role=`tool` 回复消息的 `tool_call_id` 即引用该 id，不另设适配器关联表）；未知顶层字段拒绝；由 wire 层转换进入：`function.name`→`name`、`function.arguments`（JSON 字符串）解析为对象——解析失败/重复 `tool_call` id/类型不符 → `E_MODEL_BAD_RESPONSE`；`name` ∉ 注册表 → **无动作**（注记 `unknown_tool_ignored`，不猜测映射、不报 5xx）；`arguments` **strict**：未知/多余/类型不符参数 → 拒绝该调用，无部分执行；每工具的输入/输出 JSON schema 随实现冻结；批次限额：单条助手消息 `tool_calls` ≤4，违规消息整体按 `E_MODEL_BAD_RESPONSE` 处理。

### C3.7 错误 → HTTP → UI 映射（与 SDD §4 错误分类法一致）

| 错误码 | HTTP | UI 呈现 |
| --- | --- | --- |
| E_INPUT_MISSING / E_INPUT_TYPE / E_INPUT_EXTRA | 422 | 字段级错误＋错误摘要聚焦 |
| E_INPUT_OVERSIZE | 413 | 「资料超出本应用处理范围」 |
| E_INPUT_CONTRADICTORY / E_DEDUCTION_UNSUPPORTED | 422 | 列明冲突项/不支持扣除；受影响税项拒算 |
| E_ELIGIBILITY_UNKNOWN | 422 + questions[] | 「还有 N 项资料需要确认」 |
| E_PERIOD_NOT_SUPPORTED / E_RULES_STATE_UNVERIFIABLE / E_TIME_POINT_REQUIRED | 422 | 「该期间暂不可计算」＋原因与来源入口 |
| E_BUNDLE_INCOMPATIBLE | 409 | 「该记录由不同的规则/引擎版本产生，拒绝重算」；**定义：replay 时 bundle/engine 与原 binding 不完全相等 → 409 拒绝重算（不降级、不迁移）**；历史回放 ≠ 当前重评（C3.1 replay 行） |
| E_CONFIRMATION_REQUIRED / STALE | 403 / 409 | 重新检查资料并重新确认 |
| E_CONFIRMATION_CONSUMED | 409 | 「该确认已使用」＋同 session `record_id`（如存在） |
| E_CONFIRMATION_TARGET_MISMATCH | 409 | 「请从发起确认的同一税项入口执行」；确认**不被消费** |
| E_CONSENT_REQUIRED / STALE | 403 / 409 | 重新预览并授权 |
| E_CSRF / E_ORIGIN / E_HOST | 403 | 通用安全提示（不泄漏细节） |
| E_MODEL_UNCONFIGURED | 409 | 「AI 尚未配置；本地计算可用」 |
| E_MODEL_AUTH / RATE_LIMIT / TIMEOUT / BAD_RESPONSE | 502 | 保留表单与结果；重试/继续本地 |
| E_UPDATE_IN_PROGRESS（幂等例外） | 202 | 显示现有作业进度 |
| E_UPDATE_QUARANTINED / ROLLBACK_INVALID | 409 | 隔离原因 / 无效回滚目标 |

错误码**权威全表以本节为准**；新增错误码必须先入本表，再入实现与 SDD 摘要。`E_TIME_POINT_REQUIRED` 的触发条件以税法 annex 逐条款核实为准——**不以条例 metadata 的 commencement 时刻（如 11:00）推断文书签署时分门控**，不预设具体切点示例。

## C4 模型适配与同意状态机（R12 → REQ-11/14）

- **C4.1** 仅支持固定 OpenAI 兼容 Chat Completions **wire profile**：`POST {base}/v1/chat/completions`，非流式 JSON，`tools` 必需；不接受任意插件端点。传输用 **HTTPX 直接 HTTP**（`content=frozen_bytes`），**不使用任何模型 SDK**；「兼容」指 wire 协议，不含 SDK 依赖。
- **C4.2** 配置：密钥仅 env `HKTAX_MODEL_API_KEY`（服务端）；endpoint/model 为非秘密 env；无浏览器端密钥表单。
- **C4.3** URL 校验：必须 https；拒绝 userinfo/query/fragment；拒绝跨主机重定向（`follow_redirects=False`）。
- **C4.4** 限制：请求超时 30s；响应以**流式读取累积上限 ≤1MB**（stream-read cumulative cap，超限即中止连接并归类 `E_MODEL_BAD_RESPONSE`，不无限缓冲）；history ≤20 条消息且 ≤64KB；tool 轮次 ≤8，超过即转澄清提问；单条助手消息 `tool_calls` ≤4（C3.6）。**每次外发维持独立授权**（见 C4.6）。
- **C4.5** 预览＝**确切序列化请求字节**（排除 Authorization 秘密）＋ SHA-256。
- **C4.6** 同意状态机：`prepared → granted → consumed`（发送前**原子**消费）；`revoked/expired` 为终态；TTL 5 分钟；绑定 `(consent_epoch, session_epoch, 规则绑定四元组 {bundle_id, bundle_hash, rules_schema_version, engine_version}, profile, endpoint, model, input_revision, payload hash)`——四元组与确认能力绑定（C3.2.2）同构。重发/重试/上下文或工具结果任何变化 → 新预览＋新授权。**rules 失效机制：bundle/engine 变化、发布/回滚（即四元组任一字段变化；`bundle_hash` 不含 engine_version（C2.8），纯 engine_version 变化同样触发）→ 全部未消费 consent（prepared/granted）一律 stale（`E_CONSENT_STALE`），必须新预览＋新授权**（触发矩阵见 C3.2.4；状态表见 C4.8）。**外发管线（规范性顺序，七段）**：①字段白名单构建（仅当前任务所需字段）→ ②最小化脱敏 → ③**一次** serialize（冻结字节）→ ④完整预览（确切字节＋hash，C4.5）→ ⑤用户授权 → ⑥发送前**原子** consume → ⑦HTTPX `content=frozen_bytes` 原样发送（无 SDK、**不得重新序列化**）。**默认最小化（至模型端点）**：不发送完整 export、不发送全部确认 facts、不发送任何未在当前对话中使用的资料；白名单外字段一律不进入序列化。任何一步失败或上游变化 → 丢弃本次字节，从头重建预览并重新授权。撤回仅阻断未来调用，不撤回已发内容；撤销事件在审计中注记（非伪造撤销）；consumed 请求不可回滚。单会话同一时刻仅一个活跃 AI 轮；用户停止后再次外发需新预览。Consent（外发授权）与 Confirmation（计算确认，C3.2）为**独立**能力。
- **C4.7** 错误脱敏：不展示原始上游响应体、原始用户输入回显或密钥；映射为分类错误＋恢复提示。
- **C4.8** 同意状态转移表：

| 当前态 | 事件 | 次态 |
| --- | --- | --- |
| （无） | preview 构造（冻结字节+hash） | prepared |
| prepared | 用户授予 | granted |
| granted | 发送前原子消费 | consumed（终态） |
| prepared/granted | TTL 5 分钟到期 | expired（终态） |
| prepared/granted | 用户撤回 | revoked（终态，仅阻断未来） |
| prepared/granted | 端点/模型/上下文/工具结果/输入修订/rules 变化（绑定四元组 `{bundle_id, bundle_hash, rules_schema_version, engine_version}` 任一变化，含纯 engine_version 变化）任一 | stale（作废，`E_CONSENT_STALE`）→ 必须新 preview＋新授权 |

## C5 Web 安全、会话与单实例（R13 → REQ-12/13/14）

- **C5.1** 仅绑定 `127.0.0.1:<port>`（默认 8000）。启动命令强制 `--workers 1 --no-proxy-headers`，并由受控启动入口校验绑定地址 ∈ {127.0.0.1, localhost}，**非回环绑定拒绝启动**（不依赖用户记忆；不信任 `X-Forwarded-*`，见 C5.2）。合法 Host 精确集合：`127.0.0.1:<port>`｜`localhost:<port>`；合法 Origin：`http://127.0.0.1:<port>`｜`http://localhost:<port>`，且必须与 Host 一致。
- **C5.2** 不安全方法（POST/PUT/PATCH/DELETE）：Origin 缺失／null／跨源 → 一律拒绝；且必须携带服务器 CSRF token。安全 GET：Host/会话校验，无副作用。**不信任 `X-Forwarded-*`；无 CORS。**
- **C5.3** 每次运行随机签名密钥；不透明 HttpOnly `SameSite=Strict` 会话 cookie（不含财务负载）；CSRF token 由服务器持有。
- **C5.4** 双 epoch 分离（触发级矩阵见 C3.2.4）：`session_epoch`（清空/会话过期 → ++，**删除**会话内存 records，并使全部挂起工作与确认/同意能力失效；已下载/打印的外部副本不在服务端、不受影响）与 `consent_epoch`（AI 撤回/模型配置变更 → ++，**仅**使 AI 外发授权、挂起 AI 轮与迟到 AI response 失效；**保留**本地表单、确认能力与本地计算/record——撤回 AI 授权不得破坏本地结果）。AI 负载绑定两个 epoch；税务确认绑定 `session_epoch`＋input revision＋规则绑定。`consent_epoch` 变更后到达的迟到 AI 结果一律丢弃并注记。会话：闲置 30 分钟、绝对 2 小时、并发 ≤8。清空 → 两 epoch 均 ++，多标签页经内存通知（BroadcastChannel）失效，无财务持久化。
- **C5.5** 无财务 localStorage、无持久缓存/持久日志；敏感响应 `Cache-Control: no-store`；CSP：脚本/样式仅自身、`connect-src 'self'`、无主动 HTML 内容、`frame-ancestors 'none'`。
- **C5.6** 单实例：按 canonical DB 路径取 OS 文件锁；启动先取锁再起任务；第二进程直接拒绝。重启时持久化的「运行中」作业标记 `interrupted`，不恢复、不部分发布；check/publish/rollback 三者互斥；提交时比较基线指针（指针已变 → 幂等失败重评估）；单事务原子。known-change 状态独立于指针；该状态在 **classify 完成后、publish 判定前落盘持久化**（guard 持久规则状态），进程崩溃重启后恢复并维持阻断，**回滚与重启均不清除**。
- **C5.7** 缓存与内容安全指令：

| 面 | 指令 |
| --- | --- |
| 敏感响应（计算/记录/同意/更新） | `Cache-Control: no-store` |
| 打印（/report/print） | `no-store`；`Content-Disposition: inline` |
| 下载（/records/{id}/download） | `no-store`；`Content-Disposition: attachment` |
| CSP | `default-src 'self'`；脚本/样式仅自身；`connect-src 'self'`；无内联事件；`frame-ancestors 'none'` |
| 客户端存储 | 禁财务 localStorage/持久缓存；会话仅内存 |
| 日志 | 无持久日志；内存调试日志不含金额与密钥 |

- **C5.8 持久化边界（audit 范围）**：SQLite `audit_log` 仅记录四类事件——规则数据变更（rule）、检查/调度（check，含 scheduler/reanchor 事件）、发布（publish）、回滚（rollback）；**不含** session、consent、capability 事件，**不含**任何财务 payload；会话与同意事件仅存内存（随会话消亡，崩溃即失，不持久化）。

## C6 调度时钟（R11 → REQ-17）

- **C6.1 重锚规则**：下一锚 = 最近一次**完整成功的实际完成日** + 1 格历月（月末截断）。评审确认链：锚 2026-01-31 → 到期并成功 2026-02-28 → 下一锚 **2026-03-28**；闰年成功 2028-02-29 → 下一锚 2028-03-29。**不采用「保留 31 日」变体**（圆桌建议不构成需求）。
- **C6.2** 早于到期的手动成功、或迟后完成 → 一律按**实际完成时间戳**重锚；`reanchor` 事件写入 audit_log（check/调度类，见 C5.8）。
- **C6.3** 失败/部分来源 → `global_success_at` 不推进；候选被隔离 → 检查可完成（推进成功时间）但**不发布**。
- **C6.4** 时钟可注入（测试注入假时钟）；退避 60min×2 封顶 24h，无忙循环；启动合并补查单次。
- **C6.5** 必备时钟测试：31Jan→28Feb→28Mar；闰 29Feb→29Mar；**31May→30Jun→30Jul**；失败不推进；隔离完成检查无发布；手动早完成重锚；退避无忙循环。
- **C6.6** 调度事件 → 状态转移（规范性）：

| 事件 | 状态效果 |
| --- | --- |
| 首次启动（无 global_success_at） | 立即生成检查作业（startup_initial） |
| 启动时 now ≥ next_due_at | **单次**合并补查作业（startup_catchup） |
| 到期触发 / 手动触发（已运行） | 复用现有 job（幂等，202） |
| 全部来源 fetch+parse+classify 完成 | `global_success_at` 推进；按完成日重锚 next_due_at |
| 任一来源失败/缺失 | 该源 success 为空；全局成功不推进；进入退避 |
| 候选通过全部验证 | 原子发布（独立 publish_outcome=published） |
| 候选被隔离 | publish_outcome=quarantined(reason)；检查成功仍成立 |
| 时钟注入 | 测试用假时钟；运行时读系统时钟，不轮询忙等 |

## C7 更新数据契约（R9/R10 → REQ-2/17/18）

- **C7.1 参数槽位二分**：`auto_parameter_slots`（可自动更新）＝既有 typed profile（固定 band 数/次序/运算恒等式）内的**数值**、带日期的 allowance/cap/rebate 条目、及经法律证明的兼容适用 DATES。`evidence_only_or_frozen_semantics`（**永不可自动写**）＝取整方向/单位/阶段/次序、公式、资格、band 数量/算子变更；政府页面的取整措辞、按金排除、当事方资格属**证据字段**，即使 JSON 字符串/数字也不是可写规则 DATA。每个 profile 有 `profile_ID`＋`semantic_digest` 入 bundle 哈希；每个 **auto slot** 须在 manifest 中完整限定七字段：`profile_ID`／`fullpath`（束内规范路径）／`type`／`unit`／`constraints`（值域/步长/枚举）／`anchor`（来源定位）／`proof_role`（主证/独立对应）；**frozen 或语义未知槽位一律不可写**。新 profile_ID 须变更请求 CR（已批准的新旧 profile 可在证据充分下按协议做期间映射）；语义变化一律隔离＋受影响守卫。**R1–R5 税法 profile 未完全定义：编译绑定最终税法 annex 后才能 APPROVED，不猜法律单位。**
- **C7.2 统一 manifest 可校验 schema**：所有 adapter 记录符合**版本化 JSON Schema**，`artifact_type ∈ {html_table, html_section, html_index, pdf_layout}`；记录键**恰为以下封闭键集合**（未知键拒绝、缺键不可用）：`{manifest_schema_version, source_id, url(精确), artifact_type, parser_id, parser_code_hash, anchors[](每项恰为 `{path_regex(有界路径 regex), anchor_status}`；`anchor_status ∈ {proposed, unverified, confirmed}`，状态门见 C7.3), fields[](冻结 schema 内字段名), slots[](每 auto slot **恰七字段**：`profile_ID`／`fullpath`（束内规范路径）／`type`／`unit`／`constraints`（值域/步长/枚举）／`anchor`（指向 anchors[] `path_regex` 命名空间的定位引用）／`proof_role`（主证/独立对应）；**无 `slot_id`**，见 C7.1), evidence_only_fields[](evidence_only 字段名清单；**正式入 schema**，永不可自动写，见 C7.1), legal_state_rules, proof_authority, independent_counterpart}`。**C7.10 示例与本 schema 完全同构**（键集合、类型、必填、状态枚举一致）。抽取限定：**标题/条款 section＋DOM 表格有界定位**（`html_table`/`html_section` 按标题与行标锚定；`pdf_layout` 按已知版式/页锚），**非整页猜测、非正文模糊抽取**；schema 校验失败 → 记录不可用。新发现仅来自已批准修订索引（origin/destination/path regex 核实）；**不信任整个政府域名**。
- **C7.3 抽取**：HTML 按标题/条款 section 与 DOM 表格有界抽取（非正文文本、非整页猜测）；PDF 严格已知版式/页锚；未知版式/OCR → **fail closed**，不猜测；分类按具体措施/条款进行，非整页定性。**anchor（定位）状态门**：`anchors[].anchor_status=confirmed` 仅在对应 DOM/版式验证证据落档后可置；**未证 locator（anchor）不得标 confirmed**（未证 → `proposed`/`unverified`，不得进入规则数据）。解析依赖：用户 **2026-10-07 答复「暂不增加依赖」**——`beautifulsoup4`/`pypdf` 不批准、不安装；解析选型保持 OPEN gate，**不得另立方案绕过用户决定**（C1）。
- **C7.4 `primary_direct` 自动化定义**：已批准 parser 代码 hash ＋ 官方 TLS 捕获 ＋ URL 锚点 ＋ 语义绑定 ＋ 独立证明已验证状态；**无人工发布点击**。
- **C7.5 已知 manifest 记录**（URL 取自 Annex B 台账；有效性随台账状态，未晋升 T1 者不进入规则数据）：

| source_id | url | 类型/锚点 | auto_parameter_slots ／ evidence_only(冻结) | 证明/独立对应 |
| --- | --- | --- | --- | --- |
| ird_budget | https://www.ird.gov.hk/eng/tax/budget.htm | html 正文锚点 | auto＝rebate 数值（100%、cap 3000）；evidence＝主体/暂缴边界 | 官方页面原文；counterpart＝法例文本（待） |
| ird_avd | https://www.ird.gov.hk/eng/faq/avd.htm | html_table(Q1) | auto＝2026-02-26 表内数值（既有 band 结构）；evidence＝非住宅边界规则 | counterpart＝SDO 2026 Ord 3 文本（待，B2） |
| govhk_stamp | https://www.gov.hk/en/residents/taxes/stamp/stamp_duty_rates.htm | html_table（heading 区域） | auto＝band/fee/rate 数值（固定 profile）；**evidence＝取整 ceil1/ceil100、按金排除（不可自动写）** | counterpart＝SDO 附表（待） |
| ird_sdo_index | https://www.ird.gov.hk/eng/ppr/sdo.htm | html_index | 无规则字段（发现源） | 仅作 manifest 发现，非直接规则 |
| ird_pt | https://www.ird.gov.hk/eng/faq/pty.htm | html(Q7/Q26/Q27/Q32/Q33) | 全部 evidence_only（NAV/20%/按金/利息上限/PA 示例） | counterpart＝IRO 条文（待） |
| ird_pa2026 | https://www.ird.gov.hk/eng/faq/policyaddress2026.htm | html(Q1/Q3/Q4/Q5) | entitlement 证据；法律状态 UNVERIFIED → **隔离，不发布** | — |
| ird_pam61 | https://www.ird.gov.hk/eng/pdf/pam61e.pdf | pdf_layout（页锚待批准） | auto＝薪俸税免税额/税率数值（profile 待定）；其余 evidence | counterpart＝IRD 法例＋官方已刊指南 |
| govhk_salaries_allowances | https://www.gov.hk/en/residents/taxes/salaries/allowances/allowances/7years.htm | html_table（按年度） | auto＝各年度免税额数值 | counterpart＝IRO/Sch（待） |
| ird_bus_pft | https://www.ird.gov.hk/eng/tax/bus_pft.htm | html | auto＝利得税税率/两级制数值（profile 待定）；资格=evidence | counterpart＝IRD 法例＋官方已刊指南 |

- **C7.5a 独立证明与动态发现**：独立证明＝IRD 法例＋官方已刊指南（按来源捕获案件预期；**非两 URL 重复**）。已批准动态发现仅此一条：SDO index 精确 → 同 ird 主机路径 `/eng/pdf/sdo/gazette/`，文件名有界模式 `es[12][0-9]{10,14}.pdf`（与既有文档父 PDF 名核实）；其余链接一律隔离或列入精确已知 URL 清单，**无整域通配**。新 IRO 法律 URL 须显式固定 manifest/已知来源成对；未批准 → 状态不可用。**禁止以人工点击冒充 `T1-human` 标签**：primary_direct 是自动化定义（已批准 parser＋证明/哈希），非编排者逐次手更新。快照 pin 引用字段的分类不得被覆盖（C7.8）。

- **C7.6 AUTO 资格**仅限既有语义；不得假设每个税种都有机器可读合法性；法律不确定 → 隔离。
- **C7.7 新年度扩展**：仅限同已批准税族/公式/资格/取整语义 ID ＋ 证据 ＋ 回归；动态年度解析器（初始 3 年）；否则变更请求。
- **C7.8 快照**：被发布/回滚版本引用的全文快照 **pin 至受控归档为止**；**首版（受控归档建立前）被引用快照持续 pin**，同样不适用修剪；24 份保留策略仅适用**未引用**快照；hash 去重。
- **C7.9 SOURCE 状态**（按 税种/期间/谓词，各状态显式 enter/clear）：

| 状态 | 进入（enter） | 解除（clear） | 效果 |
| --- | --- | --- | --- |
| `trusted_offline` | 网络不可达，但最近已验证快照仍现行 | 网络恢复且重验一致 | 允许计算＋warn |
| `proposal_not_current` | 分类为提案/未生效 | 刊宪＋生效核实，或提案撤回 | 现行计算继续有效；提案不作现行 |
| `related_change_uncertain` | 已知相关变更且影响/状态未核实 | 独立核验完成并发布，或确认不影响 | **阻断**受影响期间 |
| `verified_future` | 已核实未来生效（含时刻） | 到达生效期间并正常适用 | 仅生效期间适用 |
| `expired` | 官方证据明确标注失效/撤销且适用期已过 | （终态；不得宣称现行） | **阻断** |
| `unknown` | 来源与快照/网络不一致且无法判定 | 重新捕获并分类一致 | **阻断**（区别于 offline） |

每月**到期**（`next_due_at` 到达）是**调度事件**，不等于规则 `expired`；`expired` 仅由来源证据判定，到期未检查不进入任何阻断状态。unknown/known 变更均阻断受影响期间；仅 `trusted_offline` 允许继续计算。guard/known-change 状态的持久化时点见 C5.6。
- **C7.10 manifest 记录示例**（结构示意，与 C7.2 schema **完全同构**——键集合、类型、必填、状态枚举一致；数值/状态随 Annex B 晋升；slot 命名/数量/单位/约束以税法 annex 与批准 manifest 为准，不预填任何未核数值；解析选型保持 OPEN gate，用户 2026-10-07 已答复「暂不增加依赖」——不安装、不绕道，C1/C7.3）：

```json
{
  "manifest_schema_version": "1（PROPOSED；随评审冻结）",
  "source_id": "govhk_stamp",
  "url": "https://www.gov.hk/en/residents/taxes/stamp/stamp_duty_rates.htm",
  "artifact_type": "html_table",
  "parser_id": "govhk_stamp_tables_v1（PROPOSED；值在 Green 产出）",
  "parser_code_hash": "sha256:…（PROPOSED，Green 产出）",
  "anchors": [
    {"path_regex": "heading:On Transfer of Hong Kong Stock",
     "anchor_status": "unverified"},
    {"path_regex": "heading:On Transfer of Hong Kong Stock#row:(Contract note|Voluntary disposition|Other transfer)",
     "anchor_status": "unverified"}
  ],
  "fields": ["…（冻结 schema 内字段名，随税法 annex）"],
  "slots": [
    {"profile_ID": "stamp_stock_v1（PROPOSED，结构示意）",
     "fullpath": "stamps.stock.rate（拟名）", "type": "decimal_string",
     "unit": "（待税法 annex 固化）", "constraints": "（值域/步长待批准，不预填）",
     "anchor": "heading:On Transfer of Hong Kong Stock#row:Contract note",
     "proof_role": "primary_official_page"},
    {"profile_ID": "stamp_stock_v1（PROPOSED）",
     "fullpath": "stamps.stock.voluntary_disposition（拟名）", "type": "object(fixed_fee,rate)",
     "unit": "（待税法 annex 固化）", "constraints": "（待批准）",
     "anchor": "…#row:Voluntary disposition", "proof_role": "primary_official_page"},
    {"profile_ID": "stamp_stock_v1（PROPOSED）",
     "fullpath": "stamps.stock.other_transfer_fixed（拟名）", "type": "decimal_string",
     "unit": "（待税法 annex 固化）", "constraints": "（待批准）",
     "anchor": "…#row:Other transfer", "proof_role": "primary_official_page"}
  ],
  "evidence_only_fields": ["rounding_property", "rounding_lease", "deposit_exclusion"],
  "legal_state_rules": {"stock_rate_change": "effective_2023-11-17（证据字段）"},
  "proof_authority": "gov.hk 官方页面原文（heading+行标锚定）",
  "independent_counterpart": "SDO 附表（待一手核验，B4 关联）"
}
```

示例声明：本 JSON 仅为**结构示意**，与 C7.2 封闭键集合一一对应（无额外键、无缺键、无 `locator` 对象、slots 无 `slot_id`）；不含任何已核税率/费值；`anchor_status` 字段值恰为 schema 枚举成员原文（`proposed`/`unverified`/`confirmed`；本示例均取 `unverified`，中文注解一律不得写入字段值），且在 DOM 验证证据落档前一律不得置 `confirmed`；`unit/constraints` 留空表示「待税法 annex」，**不得编造**；行标/列变化 → fail closed（C7.3）。

## C8 独立参考 harness 与发布验证（R6 → REQ-15/18）

- **C8.1** 固定独立参考 harness：独立实现；**不含**生产税务/取整 helper；**不与候选共享参数对象**——独立参考参数只来自**已批准的独立 source/proof**（独立证据条目），与候选 bundle 参数对象物理分离；两个引擎一致本身**不构成证据**（`test_bad_candidate_parameters_rejected_even_if_engines_agree`）。
- **C8.2** fixture 结构化：已核事实（typed）＋ 官方来源锚点 ＋ 独立推导工作纸 ＋ 按阶段期望值；数学参考代码 hash 审计 ＋ 来源事实核验；**独立证明 ≠ 两个 URL 重复**。
- **C8.3** 静态回归 pin 历史 bundle＋期望；候选验证＝**候选实际引擎输出 vs 独立核验事实＋固定参考**（不得用旧 golden 期望配新参数）。
- **C8.4** 自动发布前的运行时验证报告：manifest hash、参考 hash、bundle hash、输入事实证明、覆盖、测试、结果、失败原因——全部通过才发布。**case 绑定**：每个 profile/期间绑定**固定 caseID 集合**（caseID 与期望值由税法 annex 的独立 Red 事实固化；**税法未定 → 集合待定，不得编造**）；必需 case **全部执行**＋每 case 附**参数证据**（独立来源/证明引用）＋**逐阶段一致**（harness 与引擎各阶段中间值一致）＋**回归 failed = 0**，否则隔离。缺任何必需 case、或报告为空/零通过 → **失败（隔离）**，不得发布；静态回归 vs 新候选条件保持 C8.3。
- **C8.5** 代码/测试/受信参考语义不可变；未来证据为 **DATA-only** 结构，不可执行、不可改模板。
- **C8.6** 发布前运行时验证报告【拟名】结构：

```json
{
  "job_id": "…", "candidate_bundle_id": "…", "candidate_bundle_hash": "sha256:…",
  "manifests": [{"source_id": "…", "parser_code_hash": "…", "anchors_verified": true}],
  "reference_harness": {"implementation_id": "…", "reference_code_hash": "…"},
  "input_facts_proof": [{"fixture": "…", "anchor": "…", "tier": "T1"}],
  "coverage": {"affected_rules": […], "regression_scope": "…"},
  "cases": {"required_case_ids": ["…（税法 annex 固化；未定不得编造）"], "executed": 0,
            "per_stage_consistent": false, "parameter_evidence_refs": []},
  "regression": {"passed": 0, "failed": 0, "failed_ids": []},
  "gate_environment": {"temp_store": true, "network": false, "user_session": false,
                       "current_pointer_written": false},
  "tests": {"passed": 0, "failed": 0, "fail_reasons": [],
            "gate": "passed>0 且必需 case 全执行＋参数证据＋逐阶段一致＋回归 failed=0；空报告=隔离"},
  "decision": "publish|quarantine", "quarantine_reason": null
}
```

- **C8.7 候选门禁运行环境（隔离）**：验证门禁在**临时 store**（temp store）执行——**无网络**、**无用户 session**、**不写 current pointer**；门禁内任何读写不触及现行 bundle 存储与会话状态；发布（指针切换）仅发生在门禁外、全验证通过后的单事务（C5.6）。隔离证据（temp store 标识、无网络断言、pointer 未变）写入 C8.6 报告 `gate_environment` 字段。

## C9 条款 → REQ 映射

| 条款 | REQ |
| --- | --- |
| C1 平台基线 | 16 |
| C2 金额/标量语法 | 9、10 |
| C3 API/状态/确认 | 2、8、11、13 |
| C4 模型 profile/同意 | 11、14 |
| C5 安全/会话/单实例 | 12、13、14 |
| C6 调度时钟 | 17 |
| C7 更新数据契约 | 2、17、18 |
| C8 参考 harness 与发布验证（含 C8.7 隔离运行门禁） | 15、18（门禁隔离另涉 13） |

## C10 新增/替换命名测试（计划，未创建；全部 P0）

| REQ | 命名测试 | 覆盖 |
| --- | --- | --- |
| 9 | test_ratio_exact_one_third_no_float | 精确分数，非二进制浮点 |
| 9 | test_share_sum_must_equal_one_exactly | 份额精确和 |
| 9 | test_share_allocation_exact_rational_until_tax_rounding_point | 分配中间值保持有理 |
| 9 | test_allocation_without_frozen_policy_returns_blocked | 分配取整未冻结（R4）→ blocked |
| 9 | test_canonical_json_rejects_duplicate_keys | 规范 JSON |
| 9 | test_bundle_hash_covers_effective_moment_period_frozen_semantics_evidence_digests；test_hashes_exclude_named_runtime_fields_not_blanket_timestamps；test_core_determinism_excludes_outer_ids_health_time；test_rank_server_derived_not_authoritative_input | 哈希覆盖/封闭字段清单（运行时信封在哈希对象之外，C2.8）/core 边界；rank 派生非权威输入（C2.9） |
| 10 | test_amount_grammar_examples_table | C2 示例表全量化 |
| 10 | test_body_1mb_items_1000_child_claims_9_nonchild_resource_bound | 载荷上限（子女申索 9／非子女受全局资源界） |
| 9 | test_canonical_formatter_fixed_point_no_exponent；test_negative_zero_all_forms_rejected | C2.10 规范格式化器（含保存/落盘值无 e/E） |
| 9/2 | test_date_wire_yyyy_mm_dd_full_match_no_coercion；test_year_key_dynamic_verified_period_resolver | C2.11 日期/年度键 |
| 8/11 | test_prepare_needs_input_no_executable_id；test_confirm_ui_session_only_llm_rejected；test_consumed_repeat_409_no_double_compute；test_input_change_invalidates_prepare_and_confirmed | C3.2 三段确认 |
| 8/11 | test_confirmation_wrong_target_not_consumed | 确认绑定执行目标；错目标 409 不消费（C3.2.2） |
| 8/13 | test_consumed_409_reports_existing_record_id | consumed 409 附同 session record_id（C3.2.3） |
| 11/14 | test_consent_epoch_revoke_preserves_local_calc_record；test_late_ai_result_discarded_after_consent_change；test_session_clear_increments_both_epochs | C5.4 双 epoch |
| 11/14 | test_consent_stale_on_rules_change | rules 变化（绑定四元组 `{bundle_id, bundle_hash, rules_schema_version, engine_version}` 任一变化，含纯 engine_version 变化）→ 未消费 consent 全部 stale（`E_CONSENT_STALE`）（C4.6/C3.2.4/C4.8） |
| 8 | test_calc_status_enum_complete_partial_needs_input_blocked | 权威状态 |
| 8 | test_balance_sign_positive_owed_negative_refund | 符号契约 |
| 8 | test_partial_no_balance_when_components_missing | 部分结果 |
| 11 | test_confirmation_ticket_ttl_5min_and_invalidation | 确认票据 |
| 11 | test_execute_requires_server_confirmation_not_llm_grant | 非授权来源 |
| 13 | test_replay_requires_pinned_bundle_and_engine_version_exact_equality（原 compatible_engine 命名替换；重算不信导出税额）；test_stale_epoch_result_cannot_download_report | 重放/失效 |
| 13 | test_published_snapshot_pinned_until_controlled_archive；test_unreferenced_snapshot_24_retention_pruning | pin/修剪 |
| 14 | test_consent_preview_exact_bytes_hash_excluding_authorization | 确切字节 |
| 14 | test_consent_consumed_atomically_before_send；test_resend_after_retry_new_grant | 状态机 |
| 14 | test_sdk_sends_captured_bytes_no_reserialization → **替换为** test_httpx_sends_frozen_bytes_no_reserialization（七段管线、无 SDK） | 原样发送（C4.1/C4.6） |
| 14 | test_model_minimal_payload_no_full_export_or_unused_facts | 默认最小化：不发送完整 export/全部确认 facts/未用信息（C4.6） |
| 11/14 | test_unknown_tool_no_action_strict_params_rejected | 未知 tool 无动作；strict 参数拒绝（C3.6） |
| 14 | test_stream_read_1mb_accumulation_cap | 响应流式累积 1MB 上限（C4.4） |
| 12/14 | test_cross_origin_unsafe_post_rejected_with_csrf；test_origin_null_rejected；test_host_mismatch_rejected | Host/Origin/CSRF |
| 14 | test_session_clear_increments_epoch_invalidates_pending | epoch |
| 14 | test_second_process_rejected_by_os_lock；test_crash_lock_interrupted_job_no_partial_publish | 单实例/崩溃 |
| 14 | test_startup_single_worker_no_proxy_headers_loopback | `--workers 1 --no-proxy-headers`；强制回环（C5.1） |
| 13/14 | test_audit_log_sqlite_scope_rule_check_publish_rollback_only | audit 仅四类；session/consent/capability 仅内存（C5.8） |
| 17 | test_scheduler_31jan_28feb_28mar_chain；test_scheduler_leap_29feb_29mar；test_scheduler_31may_30jun_30jul | 时钟链 |
| 17 | test_scheduler_quarantine_completes_check_no_publish；test_scheduler_manual_early_completion_reanchors；test_backoff_no_busy_loop | 隔离/重锚/退避 |
| 17 | test_source_states_enter_clear_and_due_is_not_expired | SOURCE enter/clear；到期≠expired（C7.9） |
| 17/18 | test_known_change_persisted_before_publish_survives_restart_and_rollback | guard 状态 classify 后 publish 前落盘；重启/回滚不清（C5.6） |
| 18 | test_update_check_in_progress_returns_202_existing_job | 幂等 202 |
| 18 | test_candidate_verified_vs_independent_reference_not_old_golden | 候选验证 |
| 18 | test_auto_slot_manifest_fields_complete_and_unverified_locator_not_confirmed | auto slot 七字段齐全；未证 locator 不可 confirmed（C7.1/C7.2/C7.3） |
| 15/18 | test_bad_candidate_parameters_rejected_even_if_engines_agree | 独立参考参数来自批准独立 source/proof；双引擎一致不作证据（C8.1） |
| 15/18 | test_missing_required_case_quarantines | 必需 case 缺失/未全执行 → 隔离（C8.4） |
| 13/18 | test_candidate_gate_isolated_from_current_store_and_network | 门禁＝临时 store／无网络／无 session／不写 current pointer（C8.7） |
| 15 | test_fixture_schema_typed_facts_anchor_worksheet_expected_stage | C8.2 fixture 结构 |
| 15 | test_runtime_validation_report_complete_before_publish | C8.6 报告完备（含 cases/gate_environment 字段） |
| 15/18 | test_publish_report_requires_passed_gt0_and_full_affected_coverage | 空报告禁止发布（C8.4） |
| 3–7 | **正向金额覆盖（P0；公式/期望由未来税法 annex 独立 Red 事实治理）**：test_salaries_allowance_eligibility_caps_by_year_and_claimant；test_salaries_standard_threshold_floor_tie；test_profits_two_tier_loss_order_mixed_partner_threshold_and_final_rounding_by_year；test_property_nav_after_rates_bad_debt_whole_assessment_share_and_rounding；test_pa_eligible_options_loss_pools_rebate_joint_allocation；test_stock_sale_voluntary_other_per_document_ceil；test_lease_fixed_indefinite_total_annual_basis_rounding；test_avd_fractional_bounds_verified_regime_prior_link | 按年度/主体的成功路径。**（2026-10-08 历史标注）**本行为一次性整改指令；其中 AVD/股票/租约/利得税/PA 部分命名**已被 Annex D D11 正向行为测试取代（见 D11 末尾对照）**，薪俸税部分仍有效；本行保留仅作历史记录，不再作为现行命名权威 |
| 3 | test_dependant_year_caps_eligibility_gated_by_bundle | 年度上限资格（P0，e-tax 就绪） |

阻断/隔离类负向测试**不**关闭 REQ-3..7 的正向覆盖（R6 核心是货币成功路径）；正向期望值以税法 annex 的独立 Red 事实治理，不得以当前 blocked 行为测试冒充成功路径。

同时**替换** SDD §10 中的「阻断声明」式测试（R6）：`test_profits_final_rounding_blocker_declared` → `test_profits_rounding_unclosed_returns_blocked_no_amounts`；`test_property_final_rounding_blocker_declared` → `test_property_rounding_unclosed_returns_blocked_no_amounts`；`test_avd_2024_window_t2_table_blocked_until_t1` → `test_avd_2024_window_blocked_no_current_fallback`；`test_avd_endpoint_decimals_refused_until_closed` → `test_avd_endpoint_decimal_case_blocked_no_amounts`；`test_stock_ordinary_transfer_computable_rate_pending_b4` → `test_stock_ordinary_transfer_blocked_until_rate_table_published`。**（2026-10-08 历史标注）本段为对 SDD §10「阻断声明」式命名的一次性整改指令，已被 Annex D D11 的正向行为测试及其末尾对照取代，见 D11；本段保留仅作历史记录，不再作为现行命名权威。**

## C11 Gate2 前置（全部任务）

M1–M8 **每个任务**的全局前置：`@plan-reviewer` 返回 APPROVED ＋ 用户明确批准 SDD ＋ Annex A（UI 设计）纳入之后，才可进入 Red。依赖接线顺序（C1）：Gate2 ＋ 用户授权安装 → **test-only** pyproject/`[dev]`/env → 第一 Red → **Green** 才加 production 依赖；API context 用 TestClient 生命周期或显式 ASGI lifespan，假件（clock/outbound/tmpDB/legalhost）进程内实现、**不增第三方库**。当前无 TDD、无代码、无测试。索引（specs/README.md）须持续记录：技术整改待 @plan-reviewer **再次**复审（2026-10-08 第二次复审 NEEDS REVISION，R10/R11/R13 文档级闭合）、解析依赖（用户 2026-10-07 已答复**暂不增加依赖**，选型 OPEN gate）、R1–R5 税法 OPEN。技术预审（docs/reviews/2026-10-07-technical-pre-review.md）≠全局审批。

## 状态与变更记录

- 本 Annex 为 Draft，未获评审 APPROVED、未获用户批准；不声称任何依赖版本已验证、任何测试已运行。
- 2026-10-07：初稿，承接首轮评审 R6–R14；R1–R5 保持未决，待并行研究整合。
- 2026-10-07（第二次修订，父任务集成更正）：子女申索上限 9（非子女受全局资源界）；canonical 定点格式化器（禁 str(Decimal)）与日期/时刻语法；prepare→confirm→execute 三段确认（needs_input/blocked 无可执行 id；consumed 重复 409）；session_epoch/consent_epoch 分离；C7 auto/evidence 槽位二分＋profile_ID/semantic_digest；移除杜撰 CSS 选择器，改 heading＋行标定位（PROPOSED parser 标注）；新增 pam61e/7years/bus_pft manifest 记录与 gazette 文件名有界发现模式；C8.4 空报告禁止发布；C10 正向金额覆盖测试。
- 2026-10-07（第三次修订，按技术预审整改，待复审）：R6——C8.1 独立参考参数只来自批准独立 source/proof、不共享候选参数对象/生产 helper；C8.4 profile/期间绑定固定 caseID 集合（税法待定不得编造）＋必需 case 全执行＋参数证据＋逐阶段一致＋回归 failed=0；C8.7 门禁临时 store／无网络／无用户 session／不写 current pointer；C8.6 报告增 cases/regression/gate_environment。R7——C2.4 整数商余精确取整、2 位小数仅限 I/O；C2.8 哈希覆盖 effective 时刻/适用期/冻结语义/证据摘要、排除逐字段列名、core/envelope 边界；C2.9 rank 服务器派生非权威输入；C2.10 保存值禁指数记法。R8——C3.2.1 prepare 完整 binding；C3.2.2 确认绑定 tax_type/schema_version/input hash/执行目标（错目标 409 不消费）；C3.2.3 consumed 409 附 record_id；C3.1 replay＝确认事实＋原 binding 重算、引擎版本完全相等；C3.7 错误表补全。R9——C7.1 auto slot 七字段、frozen 未知不可写；C7.2 统一 manifest 可校验 schema（含 html_section）；C7.3 标题/条款 section＋DOM 有界抽取、locator 状态门；C7.10 示例同步；C1 解析依赖 OPEN（beautifulsoup4/pypdf 仅候选，未批准不安装）。R10——C5.6 guard classify 后 publish 前落盘、重启/回滚不清；C7.9 SOURCE enter/clear＋到期≠过期；C7.8 首版被引用快照持续 pin；C3.2.4 技术历史契约。R12——C4.1/C4.6 七段外发管线（无 SDK、content=frozen_bytes）、默认最小化；C3.6 模型响应/工具 schema、strict 参数、未知 tool 无动作、批次限额；C4.4 1MB 流式累积上限。R13——C5.1 启动 `--workers 1 --no-proxy-headers` 强制回环；C5.8 audit 仅 rule/check/publish/rollback 四类、session/consent/capability 仅内存；C5.7 打印 inline／下载 attachment。R14——C1 Gate2→test-only→第一 Red→Green production、假件不增库、TestClient/lifespan；C11 索引记录三项待决。C10 增补/替换命名测试（REQ 映射同步）。R1–R5 保持 OPEN。
- 2026-10-07（第四次修订，parent 差异复核整改，待复审）：①依赖决定落档——用户 2026-10-07 明确答复「暂不增加依赖」：C1/C7.3/C11 由「待批准」改为「已答复暂不增加」，不批准 beautifulsoup4/pypdf、不增加/安装依赖，选型 OPEN gate、不得另方案绕过；②C3.2.4 重写为**按触发的状态矩阵**（input_revision/rules 变化仅失效当前 editor/prepared/unconsumed 能力与迟到 response、保已完成 session record 原 binding 查/下载/原 snapshot 重算；consent_epoch 仅 AI 失效；session_epoch 清空/过期**删除**会话内存 records 与挂起/能力、保已下载打印外部副本），C5.4 与 C3.1 session 行同步对齐，消除「保 record vs 报告失效」歧义；③C3.3/C3.4 明确 blocked＝`amounts` 全 `null`（**非税额 0**）＋blocked_reason＋统一错误；④C3.5 印花税笼统字段（consideration_or_value/link_facts?）改为指向 **Annex D 领域模型**（docs/design/003-hk-tax-legal-contract.md，Draft，legalwriter lane）：property 两金额+max 关系 predicate、stock 每票据 inventory、lease 真实 term/rent/premium/duplicate；本文不自行立法；⑤移除「11:00 类签署时分门控」表述：C2.8 改「以税法 annex 核实条款为准，不得以 metadata commencement 时刻推断」，C3.5 instrument_time 改条件必填，C3.7 增 E_TIME_POINT_REQUIRED 触发以条款核实、不预设切点示例。主 spec 金额示例（3940/3000/940）与 E_TIME_POINT 示例由 legalwriter lane 修正（本 lane 无写权限）；R1–R5 保持 OPEN；C8 caseID 待法律闭合不猜。
- 2026-10-08（第五次修订，按 @plan-reviewer 第二次复审整改，待复审）：第二次复审（2026-10-08）结论 **NEEDS REVISION**——R10/R11/R13 文档级闭合，其余技术/契约项按复审意见整改：①C3.5 利得税实体枚举定稿为**唯一规范模型**（`entity_kind ∈ {corporation, partnership, sole_proprietorship}`；partnership 另带逐合伙人 `partner_kind ∈ {corporation, individual}`＋利润分享比例 `share:RatioStr`；`two_tier` 事实字段＝`connected_entities[]`（基期末＋控制理由）／`no_other_election_same_year`（三态）／`election_made`（三态）），Annex D 由其 lane 照此同步；②C3.7 权威错误表纳入 `E_BUNDLE_INCOMPATIBLE`（409）：replay 时 bundle/engine 与原 binding 不完全相等 → 拒绝重算（不降级、不迁移），C3.1 replay 行同步；③blocked 金额形态唯一化：六个金额键一律显式 `null`、键不省略（删除「null／缺省」双形态），C3.3/C3.4 一致；④C2.8 重写为**哈希对象冻结**：`RuleBundleContent`／`CalculationCore` 封闭字段清单＋canonical 序列化边界，运行时信封（request_id、时间戳、warnings、questions、分页等）完全在哈希对象之外，删除开放式排除清单措辞；⑤C2.1 金额正则整数位统一最多 30 位（`{0,30}`→`{0,29}`，与「30 位整数上限」一致）；⑥C7.10 示例与 C7.2 schema **完全同构**：`anchors[]`（非 `locator` 对象，含 `anchor_status` 状态枚举）、slots 恰七字段（无 `slot_id`）、`evidence_only_fields` 正式入 schema；解析选型保持 OPEN（不安装、不绕道）；⑦consent 失效机制补齐：C4.6 绑定增 `bundle_hash`，rules 变化 → 未消费 consent 全部 stale（`E_CONSENT_STALE`），C3.2.4 触发矩阵与 C4.8 状态表一致，C10 增 `test_consent_stale_on_rules_change`；⑧C3.6 重写为双层契约：上游 wire 校验（`choices[].message`、`content` 可 null、`tool_calls[].id/type/function`、`function.arguments` 为 JSON 字符串、assistant 回传 `tool_call_id`）与内部规范消息（严格校验后 `content`/`tool_calls`）分开定义，平面 schema 不得直接用于上游验证；⑨C1 环境命令修正：获批解释器 Python 3.12/3.13（本机默认 3.14.5 超基线、禁用），命令模板 `py -3.13 -m venv .venv`（或显式路径占位）＋创建后版本检查；本批不执行安装；⑩C10 被 Annex D D11 取代的一次性整改指令加「已被取代，见 D11」历史标注（两份 review 文件原样未动）。R1–R5 保持 OPEN；未运行测试、未安装依赖、不声称评审通过。
- 2026-10-08（第六次修订，按 @plan-reviewer 第三轮复审技术侧整改，待第四轮复审）：第三轮复审结论 **NEEDS REVISION**（意见由编排者转达；**转达原文无独立编号记录**——上条 ①–⑩ 为第二次复审整改登记、已核验条目原样维持，本条不虚构 ⑪/⑫ 续号，按复审意见如实分项登记如下）。**消息协议（C3.6）**：`tool_call_id` 明确属 **role=`tool` 回复消息**（与所回复 assistant 消息 `tool_calls[].id` 一一对应；assistant 消息本身仅携带 `tool_calls` 含 id）；内部规范化消息 `tool_calls` 增 `id`、保留 wire 层调用 id（不另设适配器关联表）；「wire 形状 schema 不得直接用于上游验证」改写为「**内部规范化 schema 不得直接用于上游（模型端点）请求构造或验证**，外发一律按 C4.6 冻结字节」。**consent 失效绑定**：C4.6 绑定由单一 `bundle_hash` 升级为完整四元组 `{bundle_id, bundle_hash, rules_schema_version, engine_version}`（与确认能力 C3.2.2 同构；因 C2.8 `RuleBundleContent` 不含 engine_version、纯 engine_version 变化不改变 bundle_hash，原「hash 变化→失效」机制存在漏失效），C3.2.4 触发矩阵／C4.8 状态表／C10 `test_consent_stale_on_rules_change` 同步：四元组任一字段变化 → 未消费 consent 全部 stale（`E_CONSENT_STALE`），不得以同 hash 漏失效。**manifest 示例（C7.10）**：`anchor_status` 示例值回归 schema 枚举原文 `unverified`，中文注解移出字段值、并入示例声明（枚举 proposed/unverified/confirmed）。**跨文件同步（同批落档）**：主 spec §6/§12 第三轮裁定子项（s.12B 已闭；s.60＝评税程序、首版不模拟追加评税；joint PA 首版输出规则——权威金额＝宽减后 joint 总税额、个人份额精确 Fraction、不输出个人整元分摊税额、ΣR＝0 无税不分摊；最终取整阻断收窄为「物业/利得在官方计算器覆盖子范围之外的年度/分支」）与 specs/README.md 索引行更新；主 spec 与 specs/README.md 尾部 NUL 字节截尾清理（2551/185 个，正文未动；本文件无 NUL）。R1–R5 保持 OPEN；未运行测试、未安装依赖、不声称评审通过。
