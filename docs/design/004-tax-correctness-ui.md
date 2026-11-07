# 004 — 税负正确性阶段一：薪俸税家庭与资格 UI 契约

结论：在既有薪俸税工作台内增设渐进披露的家庭／资格输入、明确未知与缺资料状态，并沿用现有确认、结果及记录模式；本文件 Status 为 Implemented（stage1；用户 Gate 2 2026-10-14 批准，2026-10-20 P3 final5 双终验关闭），仍为设计契约、不构成完整行为或运行证据。

Status: Implemented（stage1）
Created: 2026-10-14
Updated: 2026-10-20（P3 final5 oracle PASS + acceptor ACCEPTED；stage1 实施关闭；状态 Approved→Implemented（stage1）；设计内容与视觉意图不变；历史 OPEN/视觉 B/D/F 保留；代码/测试/文档语义未变）；2026-10-19（P3-W2 文档同步：已截取 375px／1280px／200% 视觉证据供 designer 复核；视觉复核未完成，不宣称 PASS；状态仍 Approved / NOT Implemented）；2026-10-14
关联：`docs/prd/002-tax-correctness-remediation.md`（Approved，Gate 1）；`docs/design/001-hk-tax-ui.md`（Approved，Annex A 基线）

> 本文件只描述获批新需求 delta，不修改、替代或重述 Approved Annex A。用户 Gate 2 已于 2026-10-14 批准（「批准第五稿，进入实施」，仅 stage1 P1→P2→P3 严格 TDD）；本文件于 2026-10-20 由 Approved 更新为 Implemented（stage1，P3 final5 双终验）；设计内容与视觉意图不变，Implemented 仅表示 stage1 验收关闭；历史 OPEN/视觉 B/D/F 保留；仍为设计契约、非完整运行证据；无依赖/服务/秘密等额外授权。

## 1. 范围、边界与设计原则

本阶段只扩展现有薪俸税工作台中的家庭及资格输入、夫妻薪俸税事实、缺资料追问、部分结果／未知下一年度提示，以及记录步骤的可读性。沿用 `docs/design/001-hk-tax-ui.md` 的色彩 token（`#176B5B`、`#F5F3EE`）、卡片、桌面 7/5 左表单右聊天、窄屏单栏、确认对话框、焦点与结果系统；不重新设计全站。

不设计或承诺阶段 2 扣除、阶段 3 完整 PA 方案／最优比较／亏损转移、税务局裁定、完整缴款退款比较。现有 PA 页面是 API-only 说明入口；本阶段只纠正已有 PA/no-PA 方案的免税额处理，不把它描述成可填写的 PA 工作台，也不扩充完整 PA 产品流程。薪俸税表单则需实际支持新增事实录入与确认。

事实/API 属性名以协调者整合的技术契约提案为当前对齐基线，不表示 schema 或税务规则已获批。`fact-design.md` 与 `technical-design.md` 均为 NOT APPROVED；Gate 2 前不得实现。月末六个月、未核亲属及死亡／争议窄边界仍按人工路径处理，不将整个普通类别标为不支持。不得由前端猜测资格或把 unknown 改成 false。

### 设计目标

- 先显示常用核心问题；只有用户申索某类别后，才渐进披露该类别所需事实。
- 每个事实明确区分「是」「否」「不确定／尚未查明」；未填也不等于否。依法不适用与不确定是不同状态。
- 只收决定资格所需的最少资料。未申索时不询问医疗详情；不要求 HKID。受养人以会话内别名识别，不要求真实姓名。
- 准确表达资格路径，不暗示系统已替税务局批准；未核实特殊情况明确停下并说明人工核实边界。

## 2. 工作台布局与信息层级

沿用现有页面的「选择税项与期间」→「收入」→「家庭及资格」顺序。家庭及资格保持一个卡片（fieldset），拆成可折叠分组。桌面左栏宽度、右侧 AI 聊天／计算状态卡不变；家庭卡片可比收入卡长，但不把家庭字段搬到右栏。页面不因扩展而增加新的顶层导航或全屏向导。

卡片标题下说明：「只填写你希望申索或需要说明的项目。不确定可以保留为不确定；系统不会当作符合或不符合。」每个分组使用原生 `<details>/<summary>` 或等效可访问 disclosure，默认只展开婚姻状态与申索总览。已有资料的分组摘要显示「已填」「还需确认」「未申索」等文字，不仅靠颜色。用户选择申索后展开详情；若单亲／DDA等其他申索需要基础关系与资格事实，即使没有基本 child/parent claim 也询问最小事实。未申索且与任何所选路径无关时不校验、不询问医疗详情。

桌面结构示意：

```text
03 家庭及资格
婚姻状态  [单身／已婚／不确定]
已婚 → 配偶及评税事实（展开）
申索意向（可多选）
  子女 ▾  [+ 添加子女]
  父母／祖父母 ▾  [+ 添加长者]
  兄弟姊妹 ▾  [+ 添加兄弟姊妹]
  本人伤残 ▾
  单亲 ▾
  伤残受养人资格（仅有相关受养人时显示） ▾
[检查资料] [确认资料并计算]
右栏：既有 AI 引导与计算状态
```

窄屏沿用单栏自然流动；不把分组压成横向表格或横向步骤条。折叠标题可换行，添加／删除条目按钮全宽或自然换行，触控高度至少 44px。

## 3. 家庭资料与资格分组契约

### 3.1 婚姻与配偶收入／评税选择

婚姻状态至少清晰区分单身、已婚、未知；如后端契约要求分居或状态变更事实，按契约补充，不由 UI 推定。选择已婚后展开「配偶及评税资料」，先问：配偶本年度是否有薪俸收入（有／没有／不确定）；有收入时录入配偶收入及适用扣除／供款事实。夫妻双方资料应明确标注「你」和「配偶」，金额各自独立，不能只显示一个家庭合计。

薪俸候选清楚分为分别评税与联合评税；显示实际 election 和假设候选两个独立区。正常夫妻候选按技术提案 actors：separate 为本人／配偶各自单位，joint 为两人共同单位；两种都展示逐人组件及家庭总额。joint PA 不在首页显示，也不臆造个人整数分配。默认值不得暗示最佳或自动取得资格。提示：「系统会按你提供的资料检查哪些方式可用；不会只因已婚就自动给已婚免税额，也不会把联合评税说成一定较省税。」

若配偶无薪俸收入，且未单独选择个人入息课税，可能构成 s.29 分支 A 的一条路径，但不得把「已婚」或该事实单独文案成自动获额；另有联合薪俸／joint PA 路径须按各自合法条件判断。若条件不明，显示具体缺项，不说「无 AI 或未选 individual PA 任一项就符合」。薪俸页面只显示实际薪俸 election 与薪俸候选；PA 本身保持 API-only，不链接完整 PA workbench。

### 3.2 可重复受养人条目

每个受养人以会话别名展示，例如「子女 1」「长者 1」「兄弟姊妹 1」；条目可编辑类别、移除并重复新增。不得要求姓名、HKID 或住址明细来作跨条目识别。出生日期、年内期间与资格事实按本稿 §3.6 的当前契约提案录入；仅收相关路径所需资料，不擅自改用年度末年龄。提案仍须 Gate 2 批准。

每条由「类别与关系」开始，再按该类别渐进显示年龄／年内条件、供养、教育、婚姻或伤残资格等事实。字段形状按本稿 §3.6 所列 `fact-design.md`／`technical-design.md` 当前提案对齐（NOT APPROVED）；不等待未定义的后续 oracle 来决定控件。分组摘要列出会话别名和状态。对于重复添加同一人、跨本人／配偶重复申索、夫妻子女组提名及需要法定分配的情形，调用服务器校验；界面指出冲突条目并给出可执行下一步，不在浏览器自行决定归属或分摊。

### 3.3 子女

申索子女时逐项确认法定关系／未婚／受供养和相应年龄、全日制教育或伤残不能工作路径。不要询问一般居港资格；提示「子女是否居港不是这项免税额的一般条件」。适用出生年度／次年额外额按年度由规则及结果解释，不因出生信息自行推断不确定的上限。

夫妻非分居时，子女申索按整组提名概念呈现，不能把每个孩子当成双方任意重复申索。此 `household.child_nomination` 与 s.10(b) 联合薪俸责任人提名是不同事实、不同 locator；child nominee 只决定适用子女／child-DDA 的归属，不决定 joint salary 责任人。自然中文提示：「夫妻双方的子女申索需要按整组安排；同一子女不能重复计入。若双方安排有争议或需要税务局分配，请先核实，不要在这里自行拆分。」非法重复可定位到相关条目。

### 3.4 父母／祖父母

每位对象分别确认关系、居港条件、该年度适用年龄档、政府伤残津贴资格，以及供养路径。伤残津贴项问「是否符合相关政府伤残津贴资格」，不能误问成「是否已领取」；如果确实只知道领取情况，应提示领取不等于资格事实，请确认资格。

将基本供养与全年同住额外额分开、分别判定：基本额路径提示「连续至少六个月同住（没有付足额对价），或达到适用现金供养门槛」；额外额另问「是否全年连续同住」。明确六个月不是全年。不得把全年同住作为基本额的必需条件。

如同一长者涉及父母／祖父母免税额与长者住宿照顾开支，显示明确互斥说明：「同一人不能在同一方案同时申索这项免税额和长者住宿照顾开支扣除；请确认采用哪一项。」最终校验以服务器为准。

### 3.5 兄弟姊妹、单亲、本人伤残、伤残受养人

- 兄弟姊妹：仅在申索后询问关系、年龄／适用资格和用户是否承担独力或主要照顾等必要事实；仅提供供养款不等于已满足照顾条件，不把「有兄弟姊妹」自动视为符合。
- 单亲：独立折叠组，只问该年度是否符合主要／独力照顾子女等必要事实；提示此项不是每名子女各加一次。若关键事实不清楚则追问，不自动授额。
- 本人伤残：仅用户主动申索后，询问是否符合适用资格；不要求诊断、病历或伤残细节。说明这是资格判断，不是医疗评估。
- 伤残受养人：仅存在相关受养人并申索时显示资格问题，不要求医疗详情。说明符合条件时可能与相应基本受养人免税额配合；若涉及院舍扣除路径则另说明选择／互斥，不能笼统说伤残额一律不可叠加。

### 3.6 薪俸事实—控件—定位契约（技术提案对齐，非获批规则）

薪俸实际录入入口是工作台 `/`。 locator 稳定、可访问名称来自可见 label。全局请求控件只出现一次；每个年度事实的 locator 均包含 YA 与 subject／relation／claim 等 ID，避免当前年度和目标年度同名冲突。统一形式：`y-{YA}-...-{ID}`；错误定位同时使用完整字段路径及可选 `year`、`claim_id`、`subject_id`，映射不到时仍给可访问通用错误。日期期间用左闭右开编辑器；区间外／未覆盖保留 unknown。`Tri` 用原生是／否／不确定 radio，缺省表示 missing。沿用 C2.9 既有 1MB、1000 collection item 及明确约束；不新增任意日期、TF 长度或人均列表上限。

| 事实（proposal 路径） | 可见控件／可访问名称 | `data-locator` | 展示与条件 |
|---|---|---|---|
| Request: `input.year_of_assessment`, `subjects[].subject_id/birth_date`, `assumptions[]`, `hk_presence_days[]` | 唯一年度选择「评税年度」；会话别名与必要生日；明确选择 NAI／MPF 投射；按需日期／日数来源 | `request-year`, `request-subject-{ID}-alias`, `request-subject-{ID}-birth-date`, `request-assumption-{personID}-{kind}`, `request-presence-{personID}-{YA}` | 全请求只一份。ID 与生日可跨年稳定；其余家庭、收入、资格、申索事实必须显式按年。只问所需生日；不收姓名/HKID/地址。 |
| `years[YA].participants`, `household.marriage/living_apart/child_nomination` | 本年度「你／配偶」参与状态；婚姻期间／分居期间编辑器；每个婚姻期间可选会话内已登记人物并显示别名 | `y-{YA}-participant-{personID}`, `y-{YA}-marriage`, `y-{YA}-marriage-period-{periodKey}-spouse`, `y-{YA}-living-apart`, `y-{YA}-child-nomination` | `periodKey` 仅为前端期间 locator key（例如期间起始日），不是 wire ID；`years[YA].household.marriage[i].spouse_id` 错误映射到对应期间 locator。历史 spouse ID 可以已登记但不参与本年度计算；显示「历史关系对象，不是本年度参与人」，不将其加入 actors、推为收入零或跨年带入。沿用已有 MarriageTF/subject ID 表达，不新增 role/core 字段。保留已注册ID、非重叠等结构错误校验；结构有效的顺序配偶变化进入窄 manual，不改写为矛盾。 |
| `years[YA].subject_facts[subjectID].unmarried/govt_disability_allowance/full_time_education/incapacitated_for_work/ordinarily_resident_hk/both_parents_dead/death_date` | 各项「该期间是否…」三态期间编辑器；双亲已故也是期间三态；必要时死亡日期 | `y-{YA}-subject-{subjectID}-{unmarried\|daa\|education\|incapacity\|hk-residence\|both-parents-dead\|death-date}` | `subject_facts` 是 ID→事实的对象映射。只因可见申索路径需要时展示；DDA／单亲即使没有基本 claim，也须询问其 entitlement 所需的未婚、关系、年龄、供养／照顾等基础事实。没有必要医疗问题。 |
| `years[YA].relations[].kinship` | 「与本人／配偶的关系」类别、依据、关系有效期间三态 | `y-{YA}-relation-{relationID}-kinship-kind`, `...-kinship-basis`, `...-kinship-periods` | 未核关系组合进入窄 manual 个案，不把普通类别整体拒绝。 |
| `relations[].maintained/cash_maintenance/cohabit_non_full_consideration/sole_or_predominant_care` | 是否供养期间；本人现金供养额；非足额对价同住期间；主要／独力照顾期间 | `y-{YA}-relation-{relationID}-{maintained\|cash-maintenance\|cohabit\|care}` | 每 actor 分开；单亲、兄弟姊妹、基本长者资格可用所需原始事实，不依赖对应基本 claim 存在。六个月与全年额外资格分开。 |
| `years[YA].claims[]` | 纯申索意图及对象：child/parent/grandparent/sibling/single_parent/personal_disability/disabled_dependant/parent_cohabitation_additional | `y-{YA}-claim-{claimID}-{kind}-{subjectID}` | 仅引用 ID，不承载资格结论；DDA 可基于无基本 claim 的 care/relationship path。 |
| `years[YA].financials[personID].employment_income_nai/mpf_mandatory_contributions/has_assessable_salary_income/source_classification.salary` | 「薪俸净入息（按 s.12 调整后、Part 4A 前）」金额；强制 MPF 金额；薪俸 AI 是否存在三态；来源范围已确认／未知／人工 | `y-{YA}-person-{personID}-nai`, `...-mpf`, `...-salary-ai`, `...-salary-source` | 本人／配偶分开录。NAI 不是工资总额，也不是 MPF 前毛薪；NAI=0 不推出 AI=false。 |
| `assessment.actual.salary_joint_elected/salary_joint_assessed`, sparse `assessment.actual.pa[personID].mode/date` | 实际联合薪俸选择／评税事实仅在实际 joint/no_pa转换等活动消费者需要时显示。配偶 AI=false 且 s.29 其他条件可能成立时，显示相关人的实际 PA mode；日期仅在明确的实际日期消费者启用时出现 | `y-{YA}-assessment-actual-salary-joint-elected`, `y-{YA}-assessment-actual-salary-joint-assessed`, `y-{YA}-assessment-actual-pa-{personID}-mode`, `y-{YA}-assessment-actual-pa-{personID}-date` | 全未婚＋仅 `salary_separate` 时完全不显示／不组装 actual 字段。配偶 AI=true 已排除 branch A 时，不为该分支追问 PA mode，除非另一活动路径另需。actual.pa 是可省略 sparse map；缺失与 unknown 不自动写 none，出现字段仍严格校验已知冲突。未核转换不猜；非实际日期消费者不问日期。 |
| `years[YA].assessment.actual.salary_joint_nomination` / `hypothetical_salary_joint_nomination` | 两个独立的联合薪俸责任人提名控件：未指定／已指定／不确定／有争议；选择「已指定」时从本年度参与人中选本人或配偶 | `y-{YA}-assessment-actual-salary-joint-nomination-{status\|person}`, `y-{YA}-assessment-hypothetical-salary-joint-nomination-{status\|person}` | Wire 形状 `{status:"none"\|"named"\|"unknown"\|"disputed",person_id:pID\|null}`；`named` 必选本年度 participant，其余 person_id 必须为空。仅 s.10(b) 需要提名；s.10(a) 责任人由规则派生。Actual 与 hypothetical 完全分开且不互相复制、不跨年复制；不得默认本人、收入较高者或子女提名人。子女整组 `household.child_nomination` 是不同控件；它约束无基本 child claim 的 child-DDA 归属，但不约束 spouse DDA。 |
| `years[YA].financials[personID].deduction_items[]` (`item_type,amount,eligibility,subject_id,payment_ids`) | 原有扣除项金额、用户声明资格符合／不符合／未知、ERCE 对象与付款引用 | `y-{YA}-person-{personID}-deduction-client-{clientKey}-{amount\|eligibility\|subject\|payments}` | `clientKey` 是仅前端稳定 locator key，不是 wire 字段／新 ID。服务端 `field` 路径中的数组索引按当前规范化数组映射到该 clientKey locator；编辑／重排须先更新映射并防迟到错误聚焦旧行。只计算既有 `ERCE` 类型；未支持类型的正数报输入错误，显式零额占位保留旧兼容路径，显式 unknown 仍追问，不据此提供阶段2扣除控件。ERCE eligibility=true 是用户声明，不能覆盖服务器原始事实判断；false=不请求；unknown／缺失即使金额为零仍追问。显式 ERCE 意图才可进入扣除；原始 care payment 可独立支持 DDA entitlement。 |
| `years[YA].care_payments[]` | 付款人、受照顾者、付款日期／金额、照顾服务起迄、是否住宅照顾、是否付院舍／代理、院舍类别及适用期间三态 | `y-{YA}-care-{paymentID}-{payer\|subject\|paid-on\|amount\|from\|to\|residential\|paid-to-home\|home-basis\|home-periods}` | 仅相关照顾／DDA路径显示；用于原始资格判断，不自动生成扣除意图／金额。 |
| Property/business/PA-only fields in `financials` and `assessment.actual.pa` beyond above active salary-path facts | 不在 `/` 提供物业、业务、PA来源存在／居民资格、PA候选及选择或其他PA专属事实控件 | 无首页 locator；只列 API contract reference | 本阶段 PA 保持 API-only，不得由此表生成 PA workbench。薪俸页只保留真实薪俸分支会消费的 sparse actual PA mode/date，不放通用PA表单。 |
| `years[YA].assessment.actual.history` | 仅活动 salary_joint、actual 选择／转换或其他明确历史消费者需要时显示「选择记录状态」：一般／曾撤回／有争议／不确定 | `y-{YA}-assessment-actual-history` | 不再作为所有薪俸请求必填。全未婚＋仅 `salary_separate` 时不显示／不组装；活动路径缺失／unknown 才追问，不默认 ordinary。PA API-only 不意味着无关薪俸路径要问此项。 |
| `years[YA].assessment.basis/candidates/selection` | 计算基准、明确薪俸候选和选择／best意向 | `y-{YA}-assessment-basis`, `y-{YA}-salary-candidates`, `y-{YA}-salary-selection` | 这些请求选择字段仍必需；仅显示薪俸候选，不引入 PA workbench。Separate actors 与 joint 单位按现有候选表展示逐人／家庭已知金额；未知竞争者使 best=null。 |
| `years[YA].assessment.reference_date`, hypothetical PA candidate actual date consumers | PA hypothetical `pa_self`／`pa_joint` 的 P1/P2 才消费 reference_date；no_pa 及薪俸候选不消费。Actual PA 日期仅在实际 PA 日期消费者启用时消费 | 无首页 locator；API contract reference | PA 页面保持 API-only；删除主页通用／默认 reference-date 控件。请求省略不生成 Date，也不等于 unknown。活动 PA API 候选缺失时按其契约追问。 |
| `years[YA].manual_issues[]/provisional_paid` | 人工核实事项；已缴暂缴税金额 | `y-{YA}-manual-{scope}-{ID}-{code}`, `y-{YA}-provisional-paid` | 仅窄争议／来源／死亡／选择程序边界人工；任何 partial 的 balance=null，已知当前 final 可独立保留。 |

除请求控件外，所有 locator 含年度及对象 ID。不会为 PA-only 字段在主页增加控件；可见输入只覆盖薪俸工作台和该薪俸流程已有需要的事实。字段错误形状采用闭合 `field/year/claim_id/subject_id` 定位信息及通用 accessible fallback；多问题依真实控件逐项列出。`salaries.html` 仍是说明页。Technical/fact proposals 未获 Gate 2 批准。

### 3.7 评税事实按活动消费者显示

`basis`、`candidates`、`selection` 是每个请求必须明确提供的选择；这不使 `assessment.actual` 或其中所有成员变成必填。actual object 与 `actual.pa` 均可省略／稀疏；缺失、unknown 和 present 的已知事实保持区别。只将本次活动候选确实消费的字段加入表单和校验；任何已提交值仍作封闭类型与已知矛盾检查，不忽略反证、不补默认值。

- 全年婚姻事实明确为未婚、仅请求 `salary_separate`／basic 时，不论 basis 为 actual 或 hypothetical，都不显示、不组装 actual election、PA mode/date、history 或 PA reference date；不得写入 `none`、`ordinary` 或虚构日期。仅保留请求所需 basis/candidates/selection 与实际收入、规则和资格输入。
- `salary_joint_elected`／`salary_joint_assessed` 仅在实际 joint、no_pa 模式一致性／转换或相关法律消费者活动时显示。配偶 AI 已明确 true 而 s.29 branch A 因此不成立时，不为该分支询问 PA mode；若配偶 AI=false 且其他 s.29 条件可能成立，则询问被消费者要求的 actual PA mode。actual PA date 仅在已明确的真实日期消费者要求时询问；不把 reference date 当替代值。
- `actual.history` 只在活动 joint／actual 选择或转换路径确实依赖撤回／争议历史时出现。缺失／unknown 只阻断相应候选；不默认 ordinary。已知选择冲突仍需报错，不用条件显示掩盖矛盾。有限普通转换以已批准/正在审查的契约事实为界；未核 conversion 留 manual，不由 UI 猜规则。
- `reference_date` 只用于 hypothetical PA `pa_self`／`pa_joint` 的 P1/P2 消费，PA 字段在本产品维持 API-only，故不提供主页 locator。no_pa 不评估 P1，不要求参考日期；薪俸 separate/joint 不显示 PA reference date。真实 PA actual mode/date仍仅为上面明确薪俸消费者需要的事实，不构成 PA 工作台。
- 主页只呈现服务端/契约映射标记为活动的字段，不在 UI 重新实现 eligibility 算法。活动路径缺事实或 unknown 时走现有 needs_input/partial 状态及精确 locator；不活动字段完全省略。仍独立确定的 salary separate 可保留；若 joint/转换竞争候选 unknown，best=null。

### 3.8 同一评税年度的顺序配偶与 manual 出口

婚姻期间编辑器允许在每个 `married` 时段选择一个已登记 session subject ID。当前计算身份区只列 self 与本年度 participant spouse；历史期间若指向另一已登记 ID，显示其会话别名及「仅为该期间历史关系对象，不是本年度参与人」。需要添加历史对象时只用现有 subject/alias 控件；不添加 wire role、core 字段或新的 ID 种类，也不要求为非参与人填写收入、生日或申索资料。

以页面错误映射将服务器 `field=years.{YA}.household.marriage`、`year={YA}`、`subject_id={self ID}` 聚焦本年度婚姻期间编辑器；具体 `marriage[i].spouse_id` 错误聚焦对应期间配偶选择。unknown ID、自身作为 spouse、重叠区间、超出年度参与者约束等结构错误仍按服务器错误摘要处理，不转换成 manual。已注册历史 spouse 顺序变化则保留原 MarriageTF，不强迫历史人物成为 annual participant，也不把有效历史改成 identity contradiction。

当同一 YA 中出现不同的已登记历史／当前 spouse ID，依赖婚姻转换的 joint、s.29、相关PA或归属候选显示窄提示：「本年度记录了配偶身份变化；这些评税路径需要人工核实。系统不会把未参与本年度的人当作零收入，也不会把其自动加入本年度评税人。」该路径不产生可确认的 prepared/confirmation，也不显示貌似完整的家庭税额。只有真正独立已知的 separate 组件可单独展示，并明确不代表包含全部历史家庭事实的总额；不得伪造 `p3` 年度 actor／收入为零。

计划例 `test_ui_same_ya_sequential_spouses_s7_manual` 使用 `bounded-closure-design.md` §3.1（稳定公开版＝F005 §5.4）的同一 YA 全部 JSON（p1 self、p2 年度 spouse、p3 注册但不参与；p3 与 p2 的顺序 MarriageTF 区间、原有两个年度参与人及显式 assessment facts 均照原例）。预期 marriage locator 标记 S7/manual，joint 不能准备／确认，且不假称完整历史家庭税额；`p9` 未注册、spouse=p1、重叠 marriage periods、第三 participant 分别保留结构错误负例。所有只是未来计划。

### 3.9 聊天面板的本地支持范围说明

在现有首页右侧聊天卡片标题行内，提供紧凑次要按钮「支持范围」；不加顶层导航、消息气泡或新向导。其 locator 为 `chat-local-coverage-toggle`，使用可见 label、`aria-expanded` 和 `aria-controls="chat-local-coverage-panel"`。点击只展开／收起同卡片内的本地区块 `chat-local-coverage-panel`；焦点留在按钮，键盘再次按按钮收起时仍在原位。面板标题及说明为「系统支持范围（本地说明，非模型回答）」；条目和 `/coverage` 链接自然纵向排列，375px 下换行、不横向滚动，200% 放大与既有 7/5 阅读顺序及焦点不变。模型未配置／离线时仍可打开。

SSR 在既有首页路径调用 `coverage_items()` 一次，将同一数据传入纯 `chat_coverage_guidance(items)` 映射；API 与 `/coverage` 继续用同源条目。映射只读支持等级、phase、limits、tax、subtopic 与适用年度，不读用户 facts、聊天内容或模型输出。标签仅描述来源声明的有限产品范围，并附「本次计算仍须规则可用、资料齐备及资格核实」：`phase1+computable` 不能承诺个案必能计算；`needs_input` 明说缺资料且 unknown 不等于否／零；`manual_handoff` 明说人工核实／范围外并照录范围限制；`unimplemented` 和 phase2/3 明说当前不支持计算；未识别或缺失 coverage 条目标为「支持范围尚未确认，不作可计算承诺」并链接 `/coverage`。PA 使用文案：「个人入息课税是评税选择；本阶段仅有 API，网页不提供计算工作台；只比较列明的有限方案，结果为部分可算，不代表完整最优或缴款比较。」不得出现 PA 计算入口或自动计算。

本区明确为服务端本地信息而非模型回答；内容不进入 `chatHistory`／messages、不生成 tool reply，不触发 preview、grant、chat、prepare、consent、calculate 或 outbound；点击链接只导航现有 `/coverage`。不新增自由文本意图识别、endpoint、工具或模型 payload。模型自由文本仍未经确认，不能由此文案保证模型永不说错；权威支持标签与计算能力始终由本地覆盖资料及既有规则／事实门禁决定。

## 4. 未知、条件显示与校验状态

资格原始事实控件的三态文案表达事实为「是／否／不确定」，而不是服务器资格结论；空值始终表示未提供。例外是 ERCE 的旧 `eligibility` 包装字段，它记录用户声明的申索意图／判断，仍不取代服务器根据原始事实得出的资格。前端不得把空白、未申索、unknown 或“未领取”转为 false／零。只有相关申索路径打开时，才把其必需事实纳入校验。

prepare 校验需缺资料时：

1. 顶部错误摘要显示「还有 N 项资料需要确认」，列出问题及链接；不产生可执行确认。
2. 将焦点移至错误摘要；选中链接后展开对应折叠分组，并聚焦具体控件。错误旁以常用中文说明需确认什么及为何影响计算。
3. 未知资格不自动排除或纳入，也不展示貌似完整的确定税额；展示「需要补充资料」及具体未知事项。
4. 用户确认不申索后，可跳过只服务该基本申索的细节；若另一项申索依法需要该对象的基础 entitlement facts（如无基本 claim 的单亲／DDA），仍询问最小关系／供养／DAA事实。不得索要诊断或无关资料。
5. 后端无法判定特殊／争议情形时显示「这项情况暂不能由系统确定，请向税务局或税务专业人士核实」，不让用户靠选择一个随意分摊选项绕过。

提案定义的问题定位只接收封闭的 `field`、`year`、`claim_id`、`subject_id` 等获准定位信息；`field` 先按年份及 ID 解析到 locator。缺可选 ID、未知字段路径或版本不匹配时，摘要仍以可访问通用问题显示；不得猜错年度或静默丢弃。

## 5. 结果、规则未知与记录步骤

结果继续沿用 Annex A 状态及金额分项，不由前端重算。下一年度未知须显示「下一年度暂缴税：未能估算」及原因；`next_provisional=null` 不显示为零。任何 partial 的 balance 都为 null；已有依据的本年度 final 可在独立确定时保留。未知资格若影响当前 final，不显示完整确定总税或 best。

结果解释对每项家庭免税额给出「已计入／未申索／不符合／尚待确认」及简短原因。符合或金额由权威计算结果驱动，不在客户端依据原始表单推导。显示适用年度、规则版本／来源与必要假设；不称「已获批准」或「最优」。`s31A` 伤残受养人额可与适用基本额配合的结果不得因 UI 标签误呈互斥；父母六个月路径与全年额外额分别呈现。

若服务器返回未接纳联合候选的辅助比较，只能单独标示「条件式比较，非已接纳税额」；不得放进该方案当前金额、正式 scenario、已选择结果或 best。候选不接纳时不显示其条件金额为可缴税额；单独已确定的 separate/current 候选仍按服务器结果显示。

计算步骤／记录视图中，家庭事实按会话别名列出，不把别名改成真实身份。保留原确认事实、结果状态、原规则绑定及未知／假设说明。输入修改后按既有流程将当前结果标作「输入已更改，结果已失效」，需要重新检查、确认和计算；历史记录仍作为原记录查阅，不得更新成新输入。记录行应区分已确认事实、未申索项目与缺失／未知；打印和 JSON 沿用既有记录隐私原则。

## 6. 文案基线

| 场景 | 建议中文文案 |
| --- | --- |
| 家庭区说明 | 只填写你希望申索或需要说明的项目。不确定可以保留为不确定；系统不会当作符合或不符合。 |
| 配偶路径 | 请分别填写你和配偶的收入资料。系统会检查可用的评税方式，不会仅凭已婚状态自动判断。 |
| 联合评税不可判断 | 还需要确认配偶收入／评税选择相关资料，暂不能确定联合评税是否适用。 |
| 子女整组提名 | 夫妻双方的子女申索需要按整组安排；同一子女不能重复计入。若有争议，请先核实，不要自行拆分。 |
| 父母供养 | 基本供养可按连续至少六个月同住（没有付足额对价）或适用现金门槛判断；全年同住是另一项额外资格。 |
| 伤残津贴资格 | 请确认是否符合相关政府伤残津贴资格；这不等同于已经领取津贴。 |
| 缺资料 | 还需要确认这项资料，系统不会把未知当作「否」或「零」。 |
| 部分结果 | 部分结果：最终税可按已确认资料计算；下一年度暂缴税未能估算，因为该年度规则尚未核实。 |
| 未申索医疗项 | 未申索此项时，无需提供医疗资料。 |
| 院舍互斥 | 同一长者不能在同一方案同时申索这项免税额和长者住宿照顾开支扣除。 |
| 配偶路径边界 | 配偶无薪俸 AI 且未单独选择个人 PA 只是 s.29 分支 A；其余 joint salary／joint PA entitlement 依独立条件判定，不仅凭已婚授额。 |
| NAI | 薪俸净入息按 s.12 调整后、Part 4A 前；不是毛工资，也不是简单「扣除 MPF 后」的工资。 |
| 阶段范围 | PA 页面仍为 API-only；主页不新增 PA 工作台。 |

## 7. 可访问性、窄屏与交互细节

- 使用正确标题层级、fieldset/legend、可见 label；折叠控件键盘可操作并有清楚展开状态。按钮名称包含对象别名，例如「移除子女 1」，且移除前可撤销或确认，避免误删。
- 三态选项用明确文字与原生 radio/select，不以颜色或图标单独表达。提示、错误用 `aria-describedby` 关联；动态错误数与状态经 `role=alert`／`role=status` 合适播报，避免每个字段变化都重复朗读整页。
- 错误摘要可聚焦且链接真实控件；跨折叠组定位时先展开再聚焦。确认弹窗沿用既有焦点进入、Escape 关闭、焦点返回规则；不新增额外人工确认关卡。
- 375px、200% 放大及桌面 1280px 检查。家庭条目纵向排列，长说明换行，无核心横向滚动；桌面可用双列排布同一条目内简单字段，但阅读顺序仍与 DOM 顺序一致。
- 添加受养人后焦点移至新条目标题／首字段；删除后焦点移至相邻条目或添加按钮。校验／计算后焦点移至错误摘要或结果标题。折叠时不隐藏仍需修正的错误而不提示。
- 编辑或采用立即推进本地 generation 并标 dirty；服务器 epoch/revision 只按可核 ACK 更新。`POST /api/v1/calc/invalidate` 必须经下述 invalidate/prepare 单飞队列发送，body 为 `{expected_session_epoch:int,expected_input_revision:int}`，不是每次键入都并行发请求。安全中间件先验证 Host/Origin/session/CSRF；CAS 成功为 HTTP 200 `{status:"invalidated",session_epoch:int,input_revision:int}`，revision 加一；原子删除旧 prepared、失效未消费 confirmation 和该会话未消费 consent，但保留 records 与已消费能力关联。CAS 冲突为 409 `E_CONFIRMATION_STALE` 并返回当前 epoch/revision；UI 不自动接受跨标签页状态、不重试／重发，保持 dirty 且阻断。初始 epoch/revision 读自既有 meta/version 的运行时字段，不写入税务 binding/core。
- 将 invalidate 和 prepare 视为同一 editor 的状态变更队列：任一时刻最多一个 mutation 在途。键盘／控件编辑先同步推进本地 generation、标记 dirty，并立即清除旧 confirmation／prepared／consent／结果“当前有效”标记，禁用本地 confirm、calculate、tool 与 outbound；不等网络 ACK 才反映本地已更改。保存未发送的最新草稿与 generation。invalidate 在途期间只合并草稿编辑，不发送并行 invalidate 或复用旧 revision；收到其 ACK 后不自动 prepare。
- ACK 按**操作关联**处理，header 形状遵循 005：`X-Edit-Applied: 1|0`；仅 Applied=1 才有 `X-Edit-Operation-Epoch` 和 `X-Edit-Operation-Revision`，值是本请求 reservation/invalidate 所分配的版本，不是响应时 registry 的最新值。authenticated 409 的 `X-Edit-Current-Epoch/Revision` 只供显式恢复显示，绝不自动接纳；403 不带 edit-version headers、不推进版本、不读业务 body。成功 reservation／invalidate 才推进 revision，不以 HTTP 200 与否简单判断；匹配的 prepare 即使之后为 413／422（可响应时也包括500）仍以 operation ACK 确认其分配版本。prepare reservation 在认证与 If-Match CAS 后、受限读取前仅由中间件执行一次，route 不重复递增；沿用有界 1MB 读取。headers 不进入税务 body/core/binding。
- 对仍为同一活动队列操作、同一存活 epoch 的有效 Operation ACK，即使本地 generation 已更新，也必须推进**内部 acknowledged epoch/revision**，以便队列继续；generation 变旧只使响应携带的 prepared、问题、结果及 authority 视图作废，不能丢掉本操作 ACK、覆盖草稿或复活 badge。旧 epoch、已取消操作或显式 resync 之后到达的 ACK 整体忽略，不得回滚版本／generation。prepare 在途编辑时，先等该 prepare 的 operation ACK（含有版本的 422/413），丢弃旧 generation payload，再用 ACK 版本排队 invalidate；完成后等待用户重新发起 prepare，不自动 prepare／confirm／send。
- 若收到 authenticated 409、mutation ACK 丢失／中止／缺少该操作版本，进入冲突态：保留草稿、禁用能力，不重试。用户点「重新同步」后只读取 runtime version；不得从 409 Current headers 自动采纳。无论generation是否变化，resync均要求用户重新审阅草稿，再手动prepare和confirm。明确403不带版本；保留草稿并显示拒绝，不能假设已推进。上述 ACK／失败语义已随用户 Gate 2 批准（2026-10-14）；UI不另造header、API或绑定字段。
- 清空会话继续使用现有 endpoint/body，是独立 epoch boundary：本地草稿、队列及所有有效标记立即清空；服务端提案为同一 sid 原子清旧会话记录／未消费 authority、epoch加一、revision归零，并用该 clear 操作的 Operation ACK 返回分配版本（不是之后读 registry 最新值）。丢失 clear ACK 必须显式 resync；绝不恢复旧草稿或 authority。之后任何旧 epoch 响应整体丢弃。消费发生在普通 edit 前的计算可作为历史 record、不得变为 current；若清空先于旧计算完成，则该响应丢弃且不建立 record/core。此 clear 后端语义已随用户 Gate 2 批准（2026-10-14）；004不扩 API或推断其他存储行为。
- 发送若已原子消费而仍在途，字节不能撤回；普通 edit 前合法完成的旧计算只可作为历史 record，不得成为当前 editor 结果。
- AI `propose_input`／`validate_input` 唯一参数中的 `input` 是完整 HTTP envelope `{tax_type,schema_version,input}`，不是裸 facts。成功的只读检查仅形成 canonical proposal：本地显示字段差异→用户明确采用→invalidate→HTTP prepare→用户手动 confirm→calculate。proposal 不自动上传事实或工具结果；过期 AI 回复须在处理 tool calls 前丢弃。迟到本地计算若已合法消费，可保留原 record，但不得重设当前编辑结果。结果读取本地仅列保存的 scenario/selection/next-year steps，不从 raw facts 重建资格；结果若要外发，需另作最小预览和新 consent。

## 8. 可执行的真实 Playwright 验收场景（计划，不表示已运行）

实现后由协调者／测试责任人基于获批契约执行；场景使用合成资料、隔离会话和真实浏览器 UI，不调用真实个人财务资料。测试选择器依可访问名称／role 和本节 locator，不依脆弱 CSS 位置。以下均为计划，尚未运行，不表示通过。

1. **家庭区渐进披露**：打开薪俸税页，验证视觉基线卡片与右侧聊天仍在；默认仅核心状态出现。选择未申索所有受养人，检查资料可继续，DOM/可访问树无医疗字段必填；选择申索子女后相应问题出现。
2. **unknown 非 false**：选择某资格「不确定」，触发检查资料；验证错误摘要定位到对应问题、无完整确定税额且标记需补资料。改为明确否后按服务器响应更新，不出现资格金额；检查不因空值静默视为否。
3. **子女条件与整组重复**：添加两个子女条目，确认别名唯一可读、能删除／新增；选定未成年／教育／伤残路径后仅出现相应问题，不出现一般居港必填。对夫妻重复提名场景由服务器返回校验，摘要链接到两项冲突，显示整组安排自然中文提示。
4. **父母六个月／全年分离**：添加长者，确认基本供养项分别展示连续六个月同住／现金门槛，全年同住额外项单独显示。选择津贴资格时看到「符合资格」而非实际领取问题；未知时不能计算。涉及住宿照顾开支时显示同一方案互斥提示。
5. **夫妻路径**：选择已婚，验证你／配偶金额控件分别可录入；配偶无薪俸收入与评税选择事实能被表达。对联合评税条件不明确的模拟响应展示缺资料／不可用原因，不能呈现为自动最优。页面 PA 说明仍明确 API-only，不出现新增完整 PA 表单入口。
6. **本人伤残与单亲最少资料**：未申索时相关详细问题折叠且非必填；申索后资格可选是／否／未知，不出现诊断、病历或 HKID 输入。单亲提示不按每名子女累加。
7. **null 下年结果与记录**：使用测试响应中的 `next_provisional=null`，验证显示部分结果和“未能估算”，不显示 `$0`，最终税及已知分项仍按服务器结果展示；记录步骤保留年度、别名、资格状态、规则绑定及未知原因。编辑家庭资料后当前结果标失效，历史记录仍可打开。
8. **键盘与窄屏错误定位**：仅键盘展开、添加和删除条目；由错误摘要链接到一个折叠内字段，验证先展开再聚焦。对话框焦点进出及 Escape 正常；375px 与 200% 放大无核心横向滚动，状态变化可由屏幕阅读器发现。
9. **实际薪俸成组事实录入**：在 `/` 填本人和配偶 NAI、MPF、薪俸 AI 存在性及实际联合选择；检查字段不混成家庭总额，unknown 与空白不同，结果逐人及家庭合计。不得在 `salaries.html` 造输入表单。
10. **无基本申索的单亲／伤残受养人**：填独立关系、照顾／DAA 期间，只提交 `single_parent` 或 `disabled_dependant` 意图而不提交基本 child/parent claim；验证资格问题可定位且无医疗细节。
11. **s.26D 替代资格与明确申索**：录照顾付款、院舍类别及适用日期，仅有 DDA 意图时可供判断替代 entitlement 而不自动扣付款；显式扣除意图才走互斥流程。
12. **目标年未知与期间空档**：只选择 `same_income`／`same_mpf` 投射，不复制婚姻、教育、claims；部分年度 TF 空档仍 unknown；salary partial 的 balance 为 null。
13. **编辑、确认与 consent 失效**：校验 `If-Match` 三键 prepare body 不变；旧 epoch/revision 得 409，dirty 期间阻断；服务端 invalidate 线性化后旧 confirmation／未消费 consent 被拒且 records 保留。覆盖授权已原子消费但仍 in-flight、不能撤回字节的边界。
14. **聊天 proposal 和竞态**：输入为完整 HTTP envelope；proposal 本地差异→采用→invalidate→prepare→人工 confirm；无事实／工具结果自动 outbound。过期 AI 回复在 tool calls 前丢弃；迟到旧 prepare 不得存 prepared；旧本地计算只能保留 record、不改当前结果；409 不自动接纳另一标签页或重发。
15. **无基础申索的配偶 DDA**：填配偶 subject 与 DAA期间，仅有 disabled-dependant意图时验证必要 spouse/关系/assessment事实可表达；无基本 spouse claim、无医疗详情。branch A 提示同时说明这不是「任一条件即可」，联合候选走独立资格路径。
16. **当前年／目标年字段定位**：同一 subject 在当前年和显式目标年填写不同事实，验证 `y-{YA}-...-{ID}` locator、question `year` 与 `subject_id` 精确关联，request-only DOB/assumptions/presence locator 不复制进 annual controls；目标年未填关系／education 不自动继承。
17. **375px 键盘与聚焦**：实际填写本人／配偶耦合金额和家庭期间字段；键盘操作 disclosure、radio、日期区间、对话框及错误链接；375px、200% 下无核心横滚，焦点顺序正确。
18. **`test_ui_singleflight_rapid_edits_delayed_invalidate_ack`**：延迟 invalidate ACK，在等待期间快速键入并改变多个相关字段；断言只有一个 mutation 在途、generation 持续递增、草稿合并保留。核对 `X-Edit-Applied` 与仅对应操作的 `X-Edit-Operation-Epoch/Revision`；即使本地 generation 已变，当前活动操作的 ACK 仍推进内部已知版本，但不能回写草稿或旧有效状态；旧epoch／取消操作 ACK 完全忽略。ACK 后不自动 prepare。
19. **`test_ui_singleflight_edit_during_prepare_error_ack_reordering`**：prepare 在途编辑草稿，分别模拟有 Operation ACK 的 422／413及可响应的 500、迟到／重排响应；等 ACK revision 后才 invalidate，丢弃陈旧 prepared/问题/结果，绝不并发 mutation、自动确认或自动发送。断言 403 无版本头且不推进，409 Current 头只用于显式 resync；旧版本响应不能重置较新 generation。
20. **`test_ui_singleflight_cross_tab_409_explicit_resync`**：真实双标签页 CAS 409 后保留本地草稿、禁用能力；仅用户点「重新同步」读取 runtime version，之后仍须重新审阅、手动 prepare/confirm，不自动接受 server state、不重试或 resend。
21. **`test_ui_singleflight_clear_session_epoch_drops_late_replies`**：清空会话期间验证现有 clear body 保持不变、clear Operation ACK 带本操作分配的新 epoch/revision；丢失 ACK 要求显式 resync。放出旧 epoch 的 invalidate/prepare/AI/calculation 响应并断言整体丢弃；另按 Gate2提案断言 clear 先于旧计算完成时服务端丢弃completion且不建record/core。仅声明此为计划，不推演其他snapshot语义。
22. **`test_ui_salary_nomination_actual_hypothetical_and_conditional_copy`**：当年 actual 与 hypothetical salary nominee 可分别输入；缺失／unknown／disputed 与 named 定位到各自 locator，actual 不拷到 hypothetical/下一年，`none` 不默认本人。未接纳 joint 的辅助金额只能带「条件式比较，非已接纳税额」，不进入 current/best。
23. **`test_ui_child_dda_nomination_not_spouse_dda`**：无基本 child claim 的 child-DDA 仍校验 household child nominee及claim归属；spouse DDA 不显示或校验子女提名。错误摘要使用当年 field/path 定位。
24. **`test_ui_singleflight_focus_consent_no_auto_outbound`**：仅键盘快速修改／恢复及显式 resync 时焦点可见且草稿不丢；dirty/conflict 下 confirm/calculate/tool/send disabled；验证没有自动 consent 消费、AI outbound 或用户未重新审阅的 proposal 采用。
25. **`tests/e2e/test_002_household_browser.py::test_002_single_salary_no_inactive_assessment_controls`**：用 bounded closure §2.1 的合成 Q1（2025_26，NAI 240000、MPF 9000、明确全年 unmarried、仅 salary_separate）；分别覆盖 actual/hypothetical basis。`basis/candidates/selection` 与真实必要薪俸事实仍输入，但页面／wire 不出现或补 actual object、PA mode/date/history、reference_date；完整 prepare→人工 confirm→execute 计划期望 final 940，因下一年事实缺失为 partial、next及balance=null。
26. **`test_ui_conditional_assessment_unknown_keeps_known_separate`**：仅在活动候选需要时出现实际 assessment 问题；活动的配偶 AI=false 且实际 PA mode unknown 时精确追问 mode、日期仅在其真实消费者启用时问。未知 joint／转换候选不宣告不合法，保持已独立确定 separate 结果并令 best=null；inactive历史／日期不出现，不写入默认值。
27. **`tests/e2e/test_002_household_browser.py::test_002_same_ya_sequential_spouse_history_manual`**（planned，复用完整 bounded-closure-design.md §3.1／稳定公开版 F005 §5.4 同YA fixture）：p3历史 spouse 可选但不成为第三 actor，提示及错误落到 `years.2025_26.household.marriage`、year=2025_26、subject_id=p1；joint/s.29 manual时不准备／确认／显示家庭总税，仅可展示真正独立确定的 separate。另映射 `tests/engines/test_002_salaries_couples.py::test_002_sequential_spouse_change_reaches_s7_manual` 与 `::test_002_conflicting_spouse_identity_remains_contradictory`；未注册p9、自身spouse、重叠期间、额外participant仍为结构错误。
28. **`tests/ai/test_002_chat_coverage.py::test_002_chat_coverage_consumes_shared_source_and_preserves_unknown`** 与 `::test_002_chat_never_claims_stage2_stage3_or_pa_web_support`（planned）：改变同一 coverage item 后本地面板/API/coverage page 一致；unknown 不变否／零；phase2/3、unimplemented、manual及PA标签准确且不造入口。
29. **`tests/e2e/test_002_chat_browser.py::test_002_chat_local_coverage_offline_no_outbound`**（planned）：模型未配置／离线时用键盘展开 `chat-local-coverage-toggle`，断言本地panel与 `/coverage` link 可用、375px/200%可读聚焦，DOM内容来自当前SSR共享source；preview/grant/chat/outbound均0、chatHistory/messages未增加本地说明、无工具结果。另独立验证用户后来显式请求外发仍走既有冻结preview、单次grant和一次send，不夹带coverage/facts、不自动重试。

## 9. 与 PRD 的责任映射及交接

| PRD AC | 本契约承担的 UI 行为 |
| --- | --- |
| AC-1 | 网页／API／本地聊天支持范围同源准确；PA 完整工作台不冒称本阶段实现 |
| AC-2 | 三态／未知不默认、缺资料错误定位、不确定不显示完整确定金额 |
| AC-3 | 子女路径、年内条件信息交 oracle 核定、不加一般居港条件、出生额外额按规则显示 |
| AC-4 | 长者居港／年龄／津贴资格、基本供养六个月或现金门槛与全年额外额分离 |
| AC-5 | 兄弟姊妹、单亲、本人与受养人伤残渐进披露；正确说明可配合关系 |
| AC-6 | 会话别名、重复／整组提名／跨人冲突提示、长者住宿互斥；服务端权威校验 |
| AC-7 | 双方收入与评税事实可经实际薪俸表单提供；配偶无薪俸、单独 PA 与 joint 可用性反馈，不承诺最优 |
| AC-8 | 只呈现既有 PA/no-PA 方案免税额纠正范围；PA 页面 API-only，不宣称选择全集 |
| AC-9 | 下一年度规则未知时 null 明示、部分结果与缺失组件说明 |
| AC-10 | 结果、步骤、记录保持一致；未知不作零，输入变更使当前结果失效而保留历史 |
| AC-11 | 可定位补资料、编辑即时服务器失效、聊天结构化提案采用及安全流程沿用；可访问性与响应式 |

协调者负责与 Approved PRD 全覆盖、oracle 与七税调用方对账。本稿依事实与技术提案把交互具体化；两份提案已随第五稿获用户 Gate 2（2026-10-14，仅 stage1）批准进入实施，仍不构成法律意见或运行验收。当前保留窄问题为日历月月末六个月、未核亲属定义及死亡／争议／选择程序；这些个案定位人工，不把整个普通类别标不支持。ERCE 仅保留既有计算类型，声明 eligibility 不覆盖原始事实判定；未知（包括零额）需追问。其他阶段2类型的正数不得计入，未支持类型的显式零额占位沿旧兼容路径，不因此提供新扣除控件。PA 仍 API-only。其余 profile、candidate、trace、HTTP 协议及 AI 信封按 `technical-design.md` 的具体提案对齐，已随用户 Gate 2（2026-10-14，仅 stage1）批准。法律含义如有疑问应提问，不自行补规则。实现与验证责任自 2026-10-14 批准后启动（P1→P3；2026-10-20 stage1 实施关闭，Implemented）。

P3-W2 已执行真实 Chromium e2e 与 375px／1280px／200% 视觉截图（证据见 W2 EV）；视觉复核仍待 designer，不构成视觉 PASS（历史 OPEN/视觉 B/D/F 保留）；本文件状态于 2026-10-20 更新为 Implemented（stage1）。其余设计场景仍为后续可执行验收计划。
