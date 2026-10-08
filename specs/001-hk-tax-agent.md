# 001 — 香港税务计算 AI Agent 与交互界面 — 软件设计文档（SDD）

Status: Implemented
Created: 2026-10-07
Updated: 2026-10-09
PRD: docs/prd/001-hk-tax-agent.md（Approved 2026-10-07，AC-1..AC-18，含每月＋启动补查＋手动官方更新及验证后自动生效）
Annex A: docs/design/001-hk-tax-ui.md（Draft，UI/UX 设计）
Annex B: docs/research/001-hk-tax-rule-evidence.md（Evidence-only 台账：T1 一手／T2 次级／T3 算术／Blocked 分层强制）
Annex C: docs/design/002-hk-tax-technical-contract.md（Draft，**规范性技术契约**：C1 平台基线／C2 金额语法／C3 API 状态与确认／C4 模型同意／C5 安全会话／C6 调度时钟／C7 更新契约／C8 参考 harness；条款→REQ 映射见其 C9）
Annex D: docs/design/003-hk-tax-legal-contract.md（Draft，**税法语义契约**：D1 AVD 物业／D2 股票／D3 租约／D4 利得税两级制与合伙／D5 物业税／D6 PA／D7 薪俸税／D8 政策提案状态／D9 强制类别／D10 Gate2 阻断分层（**唯一权威**）／D11 命名测试计划／D12 引用来源；公式/资格/取整的规范性细化以本文为准；Cap.112/Cap.117 现行法例文本（T1-现行法例文本，2026-10-08）已并入，R1–R5 子项大幅闭合、整体仍 OPEN）
模板: specs/_template.md

> 状态声明：本文为 Draft，未获 @plan-reviewer APPROVED，未获用户 Gate2 批准，不授权任何生产代码或测试。2026-10-07 首轮评审结论 **NEEDS REVISION**（docs/reviews/2026-10-07-sdd-plan-review.md）；同日技术预审（docs/reviews/2026-10-07-technical-pre-review.md）亦 **NEEDS REVISION**：仅 R11 文档级闭合，R6–R10/R12–R14 OPEN——已按预审整改；**2026-10-08 @plan-reviewer 第二次复审：NEEDS REVISION（R10/R11/R13 文档级闭合）**，已按复审意见整改技术/契约/主 spec 条款；**2026-10-08 @plan-reviewer 第三轮复审：NEEDS REVISION（意见由编排者转达）**，已按复审意见整改（C3.6 消息协议、consent 四元组绑定、C7.10 anchor_status 枚举、s.12B 闭合、s.60 与 joint PA 首版输出规则、最终取整阻断收窄，见 §13 第六次修订）；**R1–R5 子项大幅闭合（Cap.112/Cap.117 现行法例文本 T1-现行法例文本与官方计算器行为已并入 Annex D），剩余 Gate2 阻断以 Annex D D10 为唯一权威（一项剩余——已按三年度官方计算器＋BIR57 Note 3＋整元表单惯例组合拟闭合（floor 契约＋相反证据 fail-closed），待第五轮复审确认——＋范围外注记，镜像见 §12）**；**2026-10-08 @plan-reviewer 第四轮复审：NEEDS REVISION（有序清单 8 项）**，已全部整改（误录纠正、s.7C(3) 回扣、合伙合同冻结、取整年度矩阵三年度闭合、租约输入模式、wire 协议、consent 四元组、镜像同步，见 §13 第七次修订）；**2026-10-08 @plan-reviewer 第五轮复审：NEEDS REVISION（4 项）**，已全部整改（法团/混合取整改列产品精度约定待用户 Gate2、物业评税/聚合单位冻结并更正死分支归因、工作纸条件公式、镜像同步，见 §13 第八次修订）；**2026-10-08 @plan-reviewer 第六轮复审：APPROVED**（设计合同可执行性批准；不证明产品取整约定等同 IRD 实际评税、不授权实施或安装依赖）；剩余事项＝**用户 Gate2 批准（含法团/混合产品精度约定确认项）**与 D10 所列 Gate2 确认项——**已于 2026-10-08 获用户批准**（明确含产品精度约定确认项；解析依赖维持不增加；授权按 Annex C C1 顺序安装依赖）；预审≠全局审批。R1–R5 整体仍 OPEN，不因子项闭合而解除；未晋升 T1/未经法例核验者一律不得进入 fixture 或规则数据。

## 0. 摘要

本文把 AC-1..AC-18 落为 REQ-1..REQ-18（一一对应）、选定架构、API/错误/数据契约、规则-证据-发布-调度状态机、税务原语语义闭合表、命名测试计划与 TDD 里程碑。证据与官方来源缺口集中列于 §12（Gate2 阻断项）。

## 1. 范围、强制类别与仓库现状

- 税种与期间（PRD §3.1/§3.2）：薪俸税、利得税、物业税、个人入息课税比较；直接税年度 2024/25、2025/26、2026/27（2026/27 按已确认规则快照并提示未完结）。印花税：住宅/非住宅 AVD、香港股票普通转让、普通租约；文书日期初始窗 2024-04-01..2026-10-07。
- 强制可计算类别（不得整类 information-only；Annex B D6）：MPF 强制性供款扣除；普通合伙利得税及 PA 中的合伙份额；香港股票普通买卖/转让印花税。仅复杂个案可拒算。
- 排除照 PRD §6 不变：不裁定收入来源地、会计利润调整、资格争议；复杂豁免/重组/提名/遗产与法院转让拒算。
- 仓库现状证据：现有文件仅 PRD、specs/README.md、specs/_template.md、Annex A/B；无代码、框架、提交或测试 → 绿地设计；全部命名标注【拟名】。

## 2. 架构决策（已选定，待评审确认）

| # | 决策 | 理由/边界 |
| --- | --- | --- |
| A1 | 单进程本地 Python：FastAPI + Pydantic v2 + Uvicorn；Jinja2 SSR + 少量原生 ES 模块（版本区间与测试栈见 Annex C C1） | 本地单用户、确定性、无构建链；无 SPA/云/登录/多租户 |
| A2 | 有限十进制以 Decimal（仅自规范字符串构造）、非十进制有理中间值以整数 Fraction 精确表示；float 永不进入金额链路；仅税法指定取整点以整数商余精确取整后转 Decimal（完整语法/精度/哈希契约见 Annex C C2） | AC-9 |
| A3 | SQLite 单文件，仅存规则/证据/发布/调度四类数据；无财务会话表 | AC-17/18；财务会话默认内存态 |
| A4 | HTTPX 出站适配（官方来源＋模型）；TLS 校验、不跟随重定向、超时与大小上限 | AC-14/17 |
| A5 | 测试框架【拟建】：pytest + Hypothesis + Playwright | AC-15 |
| A6 | 仅回环绑定；无 CORS；Host/Origin 校验 + 会话能力 CSRF | AC-14 |
| A7 | 无 Redis/Celery/多 worker；调度为进程内 asyncio 单飞（singleflight） | AC-17 |
| A8 | 时区 Asia/Hong_Kong（zoneinfo）；调度按格历一个日历月 | AC-17 |

## 3. 核心数据与计算契约

- `AmountStr`【拟名】：ASCII 全匹配严格语法（30 位整数上限、≤2 位小数、≤32 字符、量级上限 1e14；语法、示例判定与规范化算法以 Annex C C2.1/C2.10 为权威）；JSON number、布尔、NaN/Infinity、未知额外字段、超限载荷一律拒绝。
- 输入三态：**缺失**（键不存在）≠ **显式零**（合法）≠ **unknown**（仅资格类字段允许，触发追问）。聚合字段禁止静默零默认：扣除/免税额逐项显式，无 catch-all「其他扣除」；不支持扣除字段为正数或 unknown → 拒算受影响税项。
- 引擎为纯函数，注入不可变 RuleBundle；**结果金额全部由服务器规范计算**；请求与响应绑定完整四元组 `{bundle_id, bundle_hash, rules_schema_version, engine_version}`（revision-confirmed 绑定；prepare 返回同一完整 binding，见 Annex C C3.2.1）。
- 历史回放兼容条件＝**引擎版本完全相等**（非区间兼容）：回放接受确认事实并校验原 binding 后由引擎核心**重算**，不信任导出的税额字段；历史回放 ≠ 当前重评（不以新规则迁移旧记录）；清空会话仅清内存态，不删除已导出/打印的外部副本。
- 模型工具为只读、有界；模型建议仅生成「待确认新输入」；规则、税额、更新路径不受模型影响。
- 亏损/退款走显式字段与符号约定；缺暂缴（PST）组件 → 仅允许已知最终部分的部分结果，不得输出结欠/退款总额。

输入模型草图【拟名】（Pydantic v2，`extra="forbid"`、`strict=True`、`allow_inf_nan=False`；**不含 rank 字段**——子女名次由服务器派生、提交 rank 按 `E_INPUT_EXTRA` 拒绝，见 Annex C C2.9）：

```json
{
  "year_of_assessment": "2025_26",
  "employment_income": "240000",
  "mpf_mandatory_contributions": "9000",
  "married_status": {"value": "unknown"},
  "dependent_children": [
    {"birth_date": "2024-05-01", "residence": {"value": true}}
  ]
}
```

响应包络草图【拟名】：

```json
{
  "status": "partial",
  "binding": {"bundle_id": "…", "bundle_hash": "sha256:…",
              "rules_schema_version": "1.0.0", "engine_version": "0.1.0"},
  "amounts": {"before_reduction": "3940", "reduction": "3000",
              "final_after_reduction": "940", "provisional_paid": null,
              "next_provisional": null, "balance": null},
  "missing_components": ["provisional_paid", "next_provisional"],
  "steps": [], "evidence_refs": [], "warnings": [],
  "unsupported": [], "pending_verification": [], "questions": []
}
```

规则：分项金额字段名以 Annex C C3.4 为权威（`before_reduction／reduction／final_after_reduction／provisional_paid／next_provisional／balance`；balance 正=欠款、负=估算退税）；`amounts` 内每个键只能是有依据的规范字符串或 `null`（显示「未能估算」）；`partial` 时禁止输出 `balance` 总额。示例数值为 PAM39 Q1 的 T2 候选（2025/26：收入 240,000、MPF 9,000、累进 3,940、标准 34,650、宽减 3,000、final 940），待一手核对，**不得作为 fixture**；Q2 的 2026/27 PST 35,890 属另一组假设，不得并入。缺任一 PST 组件 → `partial` 且 `balance` 为 `null`（不输出总额）。金额语法、份额精确有理、canonical JSON、哈希对象封闭字段清单与载荷上限的**规范性**全文见 Annex C C2；税法语义细化见 Annex D D7。

## 4. API 契约【拟名】

| 端点 | 方法 | 请求→响应 | REQ |
| --- | --- | --- | --- |
| /api/v1/calc/salaries-tax | POST | SalariesTaxInput → CalculationResult | 3,8,9,10 |
| /api/v1/calc/profits-tax | POST | ProfitsTaxInput → CalculationResult | 4,8,9,10 |
| /api/v1/calc/property-tax | POST | PropertyTaxInput → CalculationResult | 5,8,9,10 |
| /api/v1/personal-assessment/compare | POST | PersonalAssessmentInput → ComparisonResult | 6,8 |
| /api/v1/calc/stamp-duty/property｜stock｜lease | POST | 各 StampXxxInput → CalculationResult | 7,9,10 |
| /api/v1/meta/coverage｜rules｜version | GET | 覆盖清单／规则与来源健康／版本 | 1,2,17,18 |
| /api/v1/update/status；/check；/jobs/{id}；/rollback | GET/POST | 状态／手动触发（同一管线）／作业／回滚 | 17,18 |
| /api/v1/ai/consent/preview；/consent；DELETE /consent/{id}；/ai/chat | POST/DELETE | 预览→授予→撤回；chat 须有效同意 | 11,14 |
| 页面 /、/coverage、/salaries、/profits、/property、/personal-assessment、/stamps/*、/rules、/updates、/settings、/about（GET）；POST /api/v1/report/print | GET/POST | Jinja2 SSR（Annex A）；打印接口以 Annex C C3.1 为权威（inline＋no-store） | 12,13 |

错误分类法【拟名】：E_INPUT_MISSING／TYPE／EXTRA／OVERSIZE（422/413）、E_INPUT_CONTRADICTORY（422）、E_ELIGIBILITY_UNKNOWN（422＋结构化问题清单）、E_DEDUCTION_UNSUPPORTED（422，拒算受影响税项）、E_PERIOD_NOT_SUPPORTED（422）、E_RULES_STATE_UNVERIFIABLE（422）、E_TIME_POINT_REQUIRED（422；仅当某税种已有**经核验的 time profile** 且该 profile 按时刻区分适用时才要求文书时刻；现无此类 profile——普通文书日期即可，见 Annex D D1.5）、E_BUNDLE_INCOMPATIBLE（409；**replay 时 bundle/engine 与原 binding 不完全相等 → 拒绝重算，不降级、不迁移**，权威定义见 Annex C C3.7）、E_CONFIRMATION_REQUIRED／STALE（403/409）、E_CONFIRMATION_CONSUMED（409，同 session 附 record_id）、E_CONFIRMATION_TARGET_MISMATCH（409，错目标不消费）、E_CONSENT_REQUIRED／STALE（403/409；**rules 变化 → 未消费 consent 全部 stale**，见 Annex C C4.6）、E_CSRF／E_ORIGIN／E_HOST（403）、E_MODEL_UNCONFIGURED（409）／AUTH／RATE_LIMIT／TIMEOUT／BAD_RESPONSE（502）、E_UPDATE_IN_PROGRESS（**202 幂等例外**，附现有 job_id）／QUARANTINED（409）／ROLLBACK_INVALID（409）。计算采用 **prepare→confirm→execute 三段**（同一 UI「确认资料并计算」一键，无新增人工门）：prepare 仅事实校验并返回**完整 binding**（`needs_input`／`blocked` **无可执行 id**）；confirm 为 UI/CSRF/session 独占处理器，产生 256-bit 可执行确认能力，绑定 `tax_type／schema_version／input hash／完整 binding／执行目标`；execute 原子消费（consumed 重复 → 409 附同 session record_id；**错目标 → 409 不消费**）；绑定分离 `session_epoch`／`consent_epoch`（撤回 AI 授权不影响本地计算，见 Annex C C3.2／C5.4）；确认是服务器能力，非客户端排名或 LLM 授权。**权威状态唯一**：`complete|partial|needs_input|blocked`；`partial` 布尔仅为派生；`blocked` 不输出任何金额（六个金额键一律**显式 `null`**、键不缺省——无「缺省」形态，**不得以 0 冒充税额**；Annex C C3.3/C3.4）。金额分项规范字段：`before_reduction／reduction／final_after_reduction／provisional_paid／next_provisional／balance`（balance 正=欠款、负=估算退税；不以重复相减重建）；端点方法、记录下载/重放、作业 202 幂等语义的权威表见 Annex C C3；错误分类法以 Annex C C3.7 为**权威全表**（本表为其摘要，冲突以更严格/更新者为准）。CalculationResult 恒带 `warnings[]`、`unsupported[]`、`pending_verification[]`、`missing_components[]`。

错误响应统一形状【拟名】：

```json
{"error": {"code": "E_PERIOD_NOT_SUPPORTED",
           "message_zh": "文书日期超出已核验支持范围（2024-04-01 至 2026-10-07）；请改选支持期间或停止计算。",
           "field": "instrument_date",
           "details": [], "binding": null}}
```

## 5. 规则、证据、发布与调度状态契约

- **RuleBundle（不可变完整束）**：冻结 schema `rules_schema_version = 1.0.0`；含各税种 DATA-only 字段、applicability（直接税年度键 2024_25/2025_26/2026_27；印花税日期区间）、每税种舍入轮廓元数据、官方证据引用（URL＋锚点＋抓取日＋内容 hash＋T1/T2 层级；四事实分离：legal_state／enacted／effective（含时刻）／applicability）。canonical SHA-256。
- **SQLite 拟名表**：`rule_bundles`、`publications`、`update_jobs`、`source_checks`、`snapshots`、`audit_log`、`current_pointer`（单行）。发布＝单事务内（插入 bundle＋publication＋指针切换），无新旧参数混用；并发计算在请求开始 pin 单一完整 bundle。
- **回滚**：仅审计化指针回退到此前**已发布**版本，写 audit_log；绝不法律时间旅行——不得把已失效旧规则宣称为现行有效；受影响期间拒算并说明。
- **调度（AC-17）**：格历一个日历月、Asia/Hong_Kong，自最近一次**完整成功检查**锚起算，月末截断（锚 1-31 → 目标月末日；例：锚 2026-01-31 → 到期 2026-02-28 → 完成后下一锚按 2026-03-28，保持月距逻辑）；首次启动即到期；启动发现逾期 → **单次合并补查**（不按错过月数重复）；手动触发与计划**同一管线**；进程内 singleflight；失败有界退避（基 60min、×2、封顶 24h、进程内不持久）。**attempt 与 success 分离**：分源与全局各自记录；任一来源缺失/失败 → 全局成功时间不推进。**checked-ok ≠ published**：检查成功与发布结果独立记录。手动/迟后完成以**实际完成日重锚**（anchor 与 re-anchor 事件显式入 audit_log，check/调度类，见 Annex C C5.8）。**到期 ≠ 过期**：`next_due_at` 到达是调度事件，不进入任何 SOURCE 阻断状态（SOURCE enter/clear 见 Annex C C7.9）；known-change/guard 状态在 classify 后、publish 判定前落盘，重启/回滚不清（Annex C C5.6）。单进程；官方检查不发送任何用户财务数据，不依赖模型 API。
- **更新管线（AC-18）**：allowlist 官方 URL（精确 https 匹配、TLS 验证、不跟随重定向、超时 30s、快照 ≤10MB、每源保留最近 24 份**全文有界快照**：URL＋抓取日＋hash）→ 解析 → 语义/法律状态分类（提案/通过/刊宪/生效/适用期分离，禁止混层）→ 候选仅限**冻结 schema 内 DATA-only 字段**（未来候选证据数据允许，固定 schema）→ 独立证据校验：候选参数对照**独立来源事实**核验（不得以同一坏候选在两个引擎互证；T2 须先晋升 T1）→ 在候选 bundle 上运行受影响＋回归测试（**测试函数/源码/已批准规格不可变**；预期值来自受信任独立参考 harness——参考参数只来自已批准独立 source/proof，不与候选共享参数对象；门禁在隔离环境运行：临时 store／无网络／无用户 session／不写 current pointer，Annex C C8.1/C8.7）→ 全部验证通过后**自动发布，无需逐次点击**；否则隔离（理由：预算提案/来源冲突/证据缺失/测试失败/语义变化/schema 违例）。下载/候选/LLM 内容永不可执行；更新程序不修改源码/测试/规格；新语义走变更请求。
- **降级行为**：离线时已验证快照可计算并带通知（notice）；已知适用变更或规则过期 → **拒绝受影响期间的现行计算**（非泛化警告）；立法状态未知子项（如 PA2026 次名 160k）按**相关期间隔离**，不自动视为已生效、也不简化为 140k 全局替换。
- **规范性补充**：调度时钟的完成日重锚链（31Jan→28Feb→28Mar；闰 29Feb→29Mar）、冻结参数白名单与 auto slot 七字段限定、统一 manifest 可校验 schema、快照 pin/修剪（首版被引用快照持续 pin）、SOURCE 状态机（enter/clear、到期≠过期）、独立参考 harness（caseID 绑定＋必需 case 全执行）与发布前验证报告、候选门禁隔离运行环境，全文见 Annex C C6–C8（R6/R9–R11 的解决条款）。

调度状态字段【拟名】（`update_jobs`/`source_checks`/`meta.rules` 暴露）：

| 字段 | 语义 |
| --- | --- |
| `global_attempt_at` | 本轮完整检查尝试开始时间（每轮唯一） |
| `global_success_at` | 仅当全部 allowlist 来源完成 fetch+parse+classify 才推进；部分失败不推进 |
| `next_due_at` | `global_success_at + 1 格历月`（月末截断；无成功则 `null`＝即到期） |
| `anchor_event` | `scheduled|startup_initial|startup_catchup|manual|reanchor`；reanchor 记录手动/迟后完成的新锚（audit_log 同步） |
| `source_checks[i].{attempt_at,success_at,http_outcome,snapshot_digest}` | 分源 attempt/success，与全局分离 |
| `publish_outcome` | `published(bundle_id)|quarantined(reason)|none`；与检查结果独立 |
| `backoff_until` | 失败退避（60min×2，封顶 24h；仅进程内） |

管线阶段（每阶段产物可审计）：

| # | 阶段 | 产物/失败处理 |
| --- | --- | --- |
| 1 | allowlist fetch | `snapshots`（全文有界快照：URL＋抓取日＋hash）；非 allowlist/重定向/超时/超限 → 来源失败 |
| 2 | parse | 结构化提取（仅数据字段）；解析失败 → 来源失败，全局成功不推进 |
| 3 | classify | legal_state/enacted/effective(+时刻)/applicability 四事实；提案措辞不得标为生效 |
| 4 | candidate build | 冻结 schema 校验；DATA-only；语义变化 → `semantic_change` 隔离＋变更请求 |
| 5 | independent validation | 候选参数对照独立来源事实（≠产生变更的同一来源/候选）；缺失 → `evidence_missing` 隔离 |
| 6 | testgate | 在候选 bundle 上运行受影响＋回归测试（测试/源码/规格不可变）；失败 → `test_failure` 隔离 |
| 7 | publish | 单事务原子指针切换＋`publications`＋audit_log；无点击确认；失败任一前置 → 保持现行版本 |

## 6. 税务原语契约与语义闭合表

数值均为 Annex B 记录，非批准数据。本节为**摘要**；公式/资格/事实/取整的规范性细化与 OPEN 分层以 Annex D（docs/design/003-hk-tax-legal-contract.md，Draft）为权威，冲突以更新/更严格者为准。**分层要求：Gate2 前**闭合每强制类别的公式/资格/适用性/取整与独立 oracle 方法；**Red 阶段**固化边界数值、独立手算工作纸（可审计）、caseID 及与更多官方例的对账；**基于已核官方事实的独立推导可作正式测试期望**（非共享生产 oracle），不以「缺官方一手 fixture」推迟或否决之。**Gate2 阻断清单以 Annex D D10 为唯一权威**（镜像见 §12）；下表「Gate2 阻断」列为其现行快照（2026-10-08 第三轮裁定后对齐）。

| 税种/子项 | 语义契约（本 SDD 固化） | 取整契约 | 证据/状态 | Gate2 阻断 |
| --- | --- | --- | --- | --- |
| 薪俸税 | 累进/标准两径计算后择低；MPF 强制供款扣除必算（PAM38 T2：5%、最低 7,100、最高相关入息 30,000/月供 1,500、年上限 18,000）；年度宽减仅作用于最终税（T1：不适用暂缴）；2024/25 起标准两级制（首 5m 15%、余 16%，Annex D D7） | **估算器契约（官方行为，非普适法定规则；三年度同构）**：①薪俸税估算器＝两径先分别 floor 再比较、返回所选径取整值（2025/26 原始件 `C:\Users\elvis\.local\share\opencode\tool-output\tool_112928bef001715yyAXNgBcxDo`；2024/25／2026/27 同构 `tool_11a08aba2001Az60uXxEBqhCmO`／`tool_11a08ab0600190JIgeaD5KzDll`；rebate cap 1500/3000/0 与附表 43 完全对齐）；②PA 计算器＝CompTP 原始值严格 `<` 比较、平局取累进、外层单次 floor（Annex D D6.2a，三年度）。两者不得静默合并；**无级距内取整** | PAM39 Q1（T2）＝40k×6=240k、MPF 9k→累进 3,940、标准 34,650、宽减 3,000、final 940；与 2026/27 PST 35,890 为**不同事实，不得混用**；Annex D D7 | 法定基础已闭（s.10/s.12B/s.13/附表 1/2/3A–3D/4＋Part 5 s.27–33，T1-现行法例文本，Annex D D7.0）；剩宽减-暂缴完整交互（Red 级）；s.12B 合并 NCI 细节已闭（Annex D D7.0；D10 已闭清单）。**详见 Annex D D7** |
| 利得税 | 法团/非法团与两级制资格须显式确认；应评税利润为用户确认事实，**不得由会计利润推断或来源裁定**；普通合伙业务可算，税额明示不等同各合伙人个人税；宽减按主体区分（T1）；两级制（2018/19 起，T1 2tr.htm Q1–Q17）：法团 8.25%/16.5%、非法团 7.5%/15%，首个 2m；以**基期结束时**判定关联（控制 >50% 股本/表决权/资本或利润，直接或间接）；同一自然人多个独资只能一个选择；夫妻权益不自动合并；混合合伙门槛按利润分享比例分摊（Q7 612,000／Q8 270,000、54,000、216,000、next PST 375,000，历史例整体保留） | 最终取整＝**非法团：三年度计算器 floor 链已闭（Annex D D4.3）；法团/混合：无适用官方证据（BIR51/52「excluding cents」＝申报精度≠评税取整，复审反例 200,010×8.25%＝16,500.825）→ 产品估算精度约定 floor（明确标注「产品约定、未经 IRD 文字证明适用于法团评税」，待用户 Gate2 确认；相反证据 fail-closed，§12-1）** | Annex B 1.12（FAQ T1；示例 T2）；Annex D D4 | 两级制/关联/混合合伙/宽减已法定或 T1-observed 闭合；法团/混合最终取整＝产品精度约定待用户 Gate2（§12-1；D4.3：raw 税额 floor、择档 floor(计税利润)、宽减 ceil 后扣）与 s14B 特殊选择细项核对；历史例不得当前化。**详见 Annex D D4** |
| 物业税 | NAV＝租金（减不可追回租金）−业主已缴差饷−20% 法定修葺免税额；**顺序（T2 PAM54）：业主付差饷先扣差饷再计 20%**；按金抵销仅限不可追回；收回租金重新计收入；**不得**扣地租/修葺/保险/利息；不套用他税宽减（T1）；PA 下按揭利息以**每物业 NAV**为限（T2 PAM55） | 官方示例为整数；取整**已冻结（观察/推导分层）**：观察＝floor 方向＋PA 计算器**输入聚合字段**单位 `floor(字段AV×0.8)→floor(×15%)`（死分支行号已更正，Annex D D6.2a）；推导＝直接物业税按 BIR57 **逐项物业**评税单位（faq pty.htm Q4「property-by-property」）→ 逐物业 `floor(NAV)→floor(NAV×15%)`；**聚合顺序不恒等**（反例：两物业 AV 各 17→逐物业合计 2 vs 聚合 4；Annex D D5.0） | Q7（120,000→NAV 96,000→税 14,400＋暂缴 14,400）、Q26/Q27 T1；PAM54/PAM55 T2；Annex D D5 | s.5/s.5B/s.7C 全法定（NAV 顺序/差饷两条件/premium 36 个月/超额坏账以前年度回扣，T1-现行法例文本）；取整与评税/聚合单位已冻结（§12-1；观察/推导分层）；剩完整个案示例（B8，Red 级）。**详见 Annex D D5** |
| 个人入息课税 | `T_i = S_i + Σ_p(V_ip − I_ip) + Σ 业务份额_i`；`I_ip = min(该人该物业已确认可扣利息, V_ip)`（逐物业，**非全局池、非整笔利息×份额**；Q33/DIPN18 ¶35/PAM55 T2）；标准上限以「reduced total income（R）」口径（DIPN18 ¶40），**不得复用薪俸税净收入口径**；累进基 `max(0, R−A)`；资格与受养人/扣除重复申索建模（basic/married 跨人互斥、eldercare vs dependent allowance、子女整笔申索）；各评税方式在**每项宽减之后**比较；资格不明→追问/拒算（**不得 unknown spouse→single**），不输出「最优」断言；joint 先宽减再按 `R_i/ΣR` 处理——首版输出规则：权威金额＝**宽减后 joint 总税额**、个人份额保留**精确 Fraction**（内部中间值）、**不输出个人整元分摊税额**、`ΣR＝0` 无税不分摊 | 2025/26 官方 PA 计算器 observed（T1，Annex D D6.2a）：rebate=ceil(cap 3000)；share=floor(tax×av/tv)；CompTP 原始比较/平局累进/外层单次 floor；PACOut41=floor(累进)、42=new_STDTP floor、43=min；物业分支 floor(**输入聚合字段**AV×0.8)→floor(×15%)（单位＝输入聚合字段，非逐物业；Annex D D6.2a）；利润份额 floor((利润−亏损)×15%)；**三年度同构（2024/25–2026/27，Annex D D6.2a；宽减上限 1500/3000/0；2026/27 IsShareTax=false 记为观察）** | Q32 T1 完整示例（442,000/已婚 264,000→NCI 178,000→累进 12,920−3,000=9,920；分开：28,800＋0；夫妻/合并事实整体保留）；PAM37 例 4 T2 | s.41/42/42A/43/100/附表 1/2/43 全法定（含亏损顺序/夫妻互抵/s42(6)/先减宽减再分摊/标准上限基数，T1-现行法例文本）；s.12B 已闭；s.60＝评税程序、首版**不模拟追加评税**（s.43(2B)「追加评税全数归原所得一方」说明引用保留）；joint 分摊余数/取整由首版输出规则消解（§12 第三轮裁定子项）；计算器行为三年度同构（D6.2a）。**详见 Annex D D6** |
| AVD 物业 | 按文书日期选表，B=max(consideration,value) 精确金额：2024-04-01..2025-02-25 Table 8（≤3m→100；3m–3,528,240→100+10%；3,528,240–4.5m→1.5%；4.5m 以上公共档）；2025-02-26..2026-02-25 Table 9（≤4m→100；4m–4,323,780→100+20%；4,323,780–4.5m→1.5%；4.5m 以上同）；2026-02-26 起住宅＝Table 9＋21,739,120–100m→4.25%、100m–109,574,470→4,250,000+30%、>109,574,470→6.5%；非住宅 Scale 3＝Table 9（>21,739,120→4.25%）。区间**下开上闭**；**不平滑/不插值/不取两公式 min**；s78（2025）／s79（2026）按日期保留三类旧文书；普通文书仅日期即可 | B 精确、**不向上取整至 100**（1999-04-01 起）；选行后税额 ceil 至 1 元 | Annex D D1（T1：原 PDF＋GovHK） | 三表端点/s78/s79/s.29/s.29G/s.29D 核心全法定（T1-现行法例文本）；s.45 与 s.29D(3) 以后细分＝**范围外注记**（不阻断普通门类）；Ord 8/2024 已并入现行文本（s.78 既有过渡）、Ord 5/2026＝现行整合本无注记（索引有条例 PDF、正文未核）→ 范围外/guard（Annex B §1.18 追加二、§5）。**详见 Annex D D1** |
| AVD 股票 | 普通买卖/转让可算（强制类别，不得 information-only）；2023-11-17 起：Contract Note **每 sold note 与每 bought note 各 0.1%**（consideration 或 value）；voluntary inter vivos 5＋0.2% value；其他转让 $5；**每份分别 ceil1**；sold/bought 不合并；other-transfer 不得替代买卖税、不得每宗自动 +5；考虑对价/价值基数已法定（Cap.117 head 2：货币代价按代价额、非货币按价值，consideration **or** value 非 max；不得移植物业 max） | 税额 ceil 至 1 元（每份文书/note 分别，T1） | Annex D D2（T1 GovHK） | head 2 全法定（0.1% consideration-or-value 非 max、$5＋0.2% value、$5；代理人/本人与转让人＋承让人责任，T1-现行法例文本）；附表 8/9/10/11A 豁免边界＝范围外。**详见 Annex D D2** |
| 租约 | 档位：不确定 0.25%（年租）；≤1 年 0.25%（**总租金，必须总额**）；>1–3 年 0.5%（年租/平均年租）；>3 年 1%；期限按周年日（恰 1 年 0.25%／1 年 1 天–3 年 0.5%／>3 年 1%），**非 days/365**；普通分支取整链＝基数 ceil100→乘率→税额 ceil1（总租金 >1 年另含官方 4 位小数年化；T1 计算器 Annex D D3.1a）；**按金不计入**；premium 必须显式；已盖章买卖协议后的相关转易契 $100（移至物业行） | 租金 ceil 至 100、税额 ceil 至 1（T1） | Annex D D3（GovHK／IRSD119 T2＋租约计算器 T1） | 档位/每 $100 或其部分（ceil100 法定基础）/premium 冲突（住宅 6.5%／非住宅 4.25%，法定闭合）/premium 无租金 same-duty/复本 head 4 低额例外＋50% 联动全法定（T1-现行法例文本）；月租×12 与总租金年化 4 位＝T1-observed 计算器契约；s.29D/45 多层链细分＝范围外。**详见 Annex D D3** |
| 暂缴税 | 最终税/宽减/已缴暂缴/下年暂缴/结欠退款分列；宽减不适用暂缴（T1）；缺 PST 组件仅部分结果且 `balance` null | 随所属税种轮廓 | budget.htm T1；B5/B6 | 宽减-暂缴完整交互（Red 级，薪俸行）；暂缴假设完整化随所属税种轮廓。**详见 Annex D D7** |

## 7. UI、AI 外发与安全（与 Annex A 一致）

- 界面按 Annex A 实施：中文；375px/1280px 关键流程；结果状态四徽标（估算完成/部分结果/需要补充资料/暂不可计算）；未知项显示「未能估算」不填零；AI 建议值标「待你确认」；编辑输入仅使**当前编辑器草稿与待确认结果**失效——已完成记录按原 binding 保持可查/下载/重放（Annex C C3.2.4），清空会话/过期仅清内存态、不删除已导出/打印的外部副本。
- **AI 外发同意**：冻结 endpoint＋**完整字节**（含 history/system/tools 全负载）；预览可展开完整内容＋hash；授权**一次性＋有效期**，且在 HTTP 发送前消费；重试/上下文/内容/端点任一变化 → 重新授权；拒绝后本地表单计算照常可用。
- 密钥仅服务端（env `HKTAX_MODEL_API_KEY` 或 OS 凭据库；配置文件在仓库外）；不进浏览器/页面/日志/导出；发送仅经 HTTPX `content=frozen_bytes`（**不使用模型 SDK**），前端/SDK 不可在预览后追加字节（发送前服务端复验 hash）；默认最小化外发：不发送完整 export／全部确认 facts／未用信息（七段管线见 Annex C C4.6）。
- loopback-only（启动强制 `--workers 1 --no-proxy-headers`，非回环绑定拒绝启动）、无 CORS；Host/Origin 校验＋签名会话 cookie（SameSite=Strict）CSRF 能力；敏感操作（calc/check/publish/clear/rollback）必须携带 token；模型与快照内容按不可信文本转义渲染；SQLite audit 仅 rule/check/publish/rollback 四类事件，session/consent/capability 事件仅内存、无财务 payload（Annex C C5.8）。

## 8. 文件布局与未来命令【拟建，本文档不执行安装】

```text
app/  main.py config.py core/(money|dates|hashing|errors)
      engines/(salaries|profits|property|personal|stamps/(property|stock|lease))
      rules/(store.py|bundle.py|schema/|allowlist.json)
      updater/(scheduler|fetcher|archive|parser|classifier|validator|testgate|publisher)
      ai/(adapters|consent|sanitize|tools)  web/(pages|templates/|static/js/)  report/render.py
tests/ unit/ engines/ hypothesis/ api/ e2e/ fixtures/official/(…＋provenance.json)
scripts/check_req_traceability.py
DB 运行时默认 %LOCALAPPDATA%\hktax-agent\rules.db（HKTAX_DB_PATH 可覆盖；仓库内无 DB）
```

未来命令【获批解释器仅 Python 3.12/3.13；本机默认解释器 3.14.5 超基线（`>=3.12,<3.14`）、**禁用**；本节为拟建说明，本文档不执行安装】：`py -3.13 -m venv .venv`（或显式 3.12/3.13 解释器路径占位）；`.venv\Scripts\python --version`（**创建后版本检查**：必须输出 3.12.x/3.13.x，否则删除重建）；`.venv\Scripts\pip install -e ".[dev]"`；`pytest -q`；`playwright install chromium`；`uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers`（非回环绑定拒绝启动；启动器校验单 worker）；`python scripts/check_req_traceability.py`。

## 9. 编号需求 REQ-1..REQ-18（与 AC-1..AC-18 一一对应）

| REQ | AC | 需求（含子条款要点） |
| --- | --- | --- |
| REQ-1 | AC-1 | 覆盖页由数据驱动呈现 PRD §3.3 盘点＋§3.1 支持矩阵（字段：税项/子项/支持级别/主管机关/官方来源/核验日/适用期间/缺口）；PA 标为评税选择；历史/撤销项目分区；未实现项无计算入口。 |
| REQ-2 | AC-2 | 计算按年度键/文书日期 pin 已验证 bundle；响应带证据引用与状态；预算提案不作现行；2026/27 显示未完结提示；超范围/日期缺失/状态不可核实 → 拒算，不回退当前年度；**普通文书仅凭日期选制度**（2025-02-26／2026-02-26 不要求时刻；仅当某已核验 time profile 明确按时刻适用时才要求文书时刻，现无此类 profile）；s78／s79 仅按 before-day／同双方同条款／符合旧协议的谓词保留旧制（Annex D D1.5）；已知未核变更影响期间 → 强制 pending 标示，不得以旧规则声称符合新规。 |
| REQ-3 | AC-3 | 薪俸税：基本/已婚/子女（含新生/出生次年 s.31(1A)/(1B)）/受养父母祖父母/兄弟姊妹/单亲/伤残相关免税额＋MPF 强制供款扣除，逐项资格与上限；无 catch-all 其他扣除；两径择低＋年度宽减；资格未知→追问/拒算（法定基础 s.10/s.13/附表 1/2/3A–3D/4＋Part 5 s.27–33；**详见 Annex D D7**）。 |
| REQ-4 | AC-4 | 利得税：法团/非法团与两级制资格显式确认；输入形态以 Annex C C3.5 **唯一规范模型**为权威（`entity_kind` 三值；partnership 逐合伙人 `partner_kind`＋利润分享比例；`two_tier` 事实字段 `connected_entities[]`/`no_other_election_same_year`/`election_made`）；应评税利润为确认事实（不推断会计利润、不裁来源）；普通合伙业务可算并明示不等同合伙人个人税；亏损/扣除显式字段；宽减按主体/每业务区分（s.100(3)「每业务一个宽减、不论 PA」，Annex D D6.0；**详见 Annex D D4**）。 |
| REQ-5 | AC-5 | 物业税：NAV 口径（s.5(1A)：仅业主**同意且已缴**差饷可扣、20% 按（AV−差饷）计、不可追回租金 s.7C 含**超额部分以前年度回扣**（s.7C(3)）、按金抵销限不可追回、不得扣地租/修葺/保险/利息）；份额 s.42(1)(a) 但书；不套用他税宽减（附表 43 无物业税行）（**详见 Annex D D5**）。 |
| REQ-6 | AC-6 | PA：仅比较依法可选方式；合并入息＋NAV＋自营业份额；利息以各物业 NAV 份额为上限；标准上限按 PA 减少后总入息口径（非薪俸税复用）；重复申索阻断；宽减后比较各方案；资格不明不输出最优（法定基础 s.41/42/42A/43/100/附表 1/2/43；**详见 Annex D D6**）。 |
| REQ-7 | AC-7 | 印花税：住宅/非住宅 AVD、股票普通转让、租约按**文书日期**选制度（普通文书仅日期；过渡保留按 s78／s79 谓词，见 Annex D D1.5）；计税基数/税率表/取整全部由 bundle 数据驱动；AVD 三表与 Scale 3、股票 head 2、租约档位/取整链/premium 冲突/复本 head 4 均已法定化（Annex D D1–D3，T1-现行法例文本）；核证（s.29/s.29G）与转易链谓词缺事实 → **追问**（不得默认；Annex D D1.6/D1.7）；特殊豁免/复杂个案按 PRD 拒算；初始窗 2024-04-01..2026-10-07（**详见 Annex D D1–D3**）。 |
| REQ-8 | AC-8 | 最终税、宽减、已缴暂缴、下年暂缴、结欠/退款分列；宽减不适用暂缴；缺 PST 组件仅给已知最终部分＋原因，无总额；不显示伪精确。 |
| REQ-9 | AC-9 | Decimal 规范字符串；确定性：同（输入,bundle,engine）字节一致；请求/响应绑定版本哈希；LLM 文本不能改规则/税额；每税种舍入轮廓元数据存 bundle 并按序执行。 |
| REQ-10 | AC-10 | 三态输入（缺失/零/unknown）；类型严格（布尔/非有限/额外字段/超限拒绝）；矛盾资料结构化拒算；亏损/退款显式字段；不支持扣除为正或 unknown → 拒算受影响税项；拒算响应含中文问题清单。 |
| REQ-11 | AC-11 | 意图限于受支持任务；逐步追问复用问题清单；计算前结构化确认；解释金额/年度/引用必须来自工具结果；输入变更→**编辑器草稿与待确认结果**失效（已完成记录按原 binding 保留可查/下载/重放，Annex C C3.2.4）；无来源税率/绕过规则拒绝；新工具建议须确认。 |
| REQ-12 | AC-12 | 按 Annex A 实现 SSR 页面与状态；本地表单无模型可用；375/1280、标签、焦点、无横向滚动阻塞；输入/AI 建议/已确认/结果视觉区分。 |
| REQ-13 | AC-13 | record 含税项/期间/输入摘要/bundle＋engine 版本/步骤/税率/扣除/宽减/暂缴假设/官方出处/未覆盖/免责；JSON 下载＋打印；同快照可重现；回放兼容＝引擎版本完全相等（按确认事实＋原 binding 校验重算，不信导出税额）；回放≠当前重评；**已完成记录不因输入/规则变更而失效**；默认不含敏感信息；清空会话/过期不删外部打印副本。 |
| REQ-14 | AC-14 | 本地计算独立于模型；同意协议冻结完整字节（含 history/system/tools）＋hash＋一次性＋发送前消费＋变更即失效；发送经 HTTPX `content=frozen_bytes`（无 SDK）；默认最小化外发（不发送完整 export/全部确认 facts/未用信息；响应流式累积 ≤1MB）；密钥仅服务端；失败分类不泄密不丢表单；会话内存态可清空；无静默跨主机重定向。 |
| REQ-15 | AC-15 | **分层要求**：Gate2 前闭合每强制类别的公式/资格/适用性/取整与独立 oracle 方法（§6/§12）；Red 阶段固化边界数值、独立手算工作纸（可审计）、caseID 及与更多官方例的对账。**基于已核官方事实的独立推导可作正式测试期望**（来源锚点＋手算；非共享生产 oracle、非参考项目反推），不以「缺官方一手 fixture」推迟或否决之；official fixture 本身必须 T1 溯源（官方 URL＋锚点＋抓取日＋层级＋完整输入输出＋中间步骤；官方事实整体保留，如 Q32 夫妻合并）；禁无已核事实依据的自生成/参考项目/LLM/台账表格反推；覆盖矩阵（零值/临界/边界/资格/宽减/暂缴/拒算）；追溯脚本＋@acceptor。 |
| REQ-16 | AC-16 | 运行说明（安装/启动/模型配置与禁用/支持期间/维护/免责）；重用参考项目（pin 94e4ddca53d792316e6a53b2fb2fa8cc9e571a91）保留 MIT 版权（Copyright (c) 2018 leeyc0）与记录；依赖许可清单；不宣称 IRD 认证。 |
| REQ-17 | AC-17 | §5 调度契约全量：月历＋月末截断＋锚定/重锚显式；首启即查；逾期单次合并补查；手动同管线＋singleflight＋有界退避；分源/全局 attempt-success 分离、部分失败不推进全局成功；checked-ok≠published；UI 显示尝试/成功/下次/版本；不发财务数据、不依赖 LLM；单进程。 |
| REQ-18 | AC-18 | §5 管线契约全量：DATA-only 冻结 schema 候选；四事实分离；独立来源事实校验（禁同坏候选互证）；候选上跑不可变测试；全验证自动发布（无点击）；否则隔离；原子指针＋并发 pin；回滚仅审计化指针、不法律时间旅行；离线已验证快照带通知；已知变更/过期→拒绝受影响现行计算（PA2026 160k 期间隔离，不简化 140k）；更新程序不改源码/测试/规格；范围扩展须协议声明兼容语义。 |

## 10. REQ → 命名测试映射（计划，未创建任何测试）

| REQ | 命名测试（pytest 计划名） |
| --- | --- |
| 1 | test_coverage_page_lists_inventory_with_status；test_personal_assessment_labelled_election；test_unimplemented_no_calc_entry |
| 2 | test_year_pins_verified_bundle；test_stamp_table_by_instrument_date；test_stamp_2025_02_26_ordinary_instrument_date_only_computable；test_pending_change_marks_affected_period；test_out_of_range_refused_no_current_fallback |
| 3 | test_salaries_mpf_mandatory_deduction；test_salaries_two_path_floored_compare_selected_floor；test_salaries_reduction_3000_cap_final_only；test_salaries_unknown_eligibility_questions；test_salaries_official_example_pam39_q1_pending_t1_promotion |
| 4 | test_profits_corporate_vs_unincorporated；test_profits_two_tier_confirmed_gating；test_profits_connected_control_gt_50_equity_vote_capital_profit；test_partnership_ordinary_business_computable；test_partnership_share_fact_not_personal_tax；test_partnership_mixed_threshold_apportioned_ratios_q7_612000；test_partnership_loss_offset_then_pa_transfer_q8_270000_54000_216000_next_pst_375000；test_profits_rounding_unclosed_returns_blocked_no_amounts |
| 5 | test_property_q7_example_120000_96000_14400；test_property_rates_owner_paid_only；test_property_rejects_ground_rent_repairs_insurance_interest；test_property_rounding_unclosed_returns_blocked_no_amounts |
| 6 | test_pa_q32_example_9920_vs_28800；test_pa_income_nav_business_share_combined；test_pa_interest_cap_per_property_nav_share；test_pa_standard_cap_pa_reduced_income_not_salary_reuse；test_pa_unknown_spouse_not_defaulted_single；test_pa_joint_rebate_then_ratio_apportion_example4_28896_7224；test_pa_calc_comp_raw_strict_compare_tie_progressive_single_floor；test_pa_duplicate_claim_blocked；test_pa_unknown_qualification_no_optimal_claim |
| 7 | test_stamp_property_table8_bands_in_window；test_stamp_property_table9_bands_2025_02_26_to_2026_02_25；test_stamp_property_2026_residential_high_tiers_100m_and_109574470；test_stamp_property_scale3_nonresidential_above_21739120；test_stamp_property_base_is_max_consideration_value_no_round_100_then_duty_ceil_1；test_stamp_property_row_selection_lower_exclusive_upper_inclusive；test_stamp_property_thresholds_t_plus_minus_0_01_positive；test_stamp_2025_02_26_ordinary_instrument_date_only_computable；test_stamp_ord12_s78_preserves_only_executed_supersedes_conform_predicates；test_stamp_ord3_2026_s79_additional_duty_not_old_rates_before_gazettal；test_related_conveyance_100_after_stamped_agreement_requires_chain_predicates；test_stock_contract_note_sold_and_bought_each_ceil_1；test_stock_contract_note_basis_consideration_or_value_not_max；test_stock_voluntary_inter_vivos_5_plus_0_2pct_value_ceil_1；test_stock_other_transfer_5_each_not_sale_total_5_no_auto_plus_5；test_lease_exact_1_year_0_25_vs_1y1d_0_5；test_lease_exact_3_year_0_5_vs_3y1d_1；test_lease_rent_basis_ceil_100_then_duty_ceil_1_deposit_excluded；test_lease_premium_with_rent_residential_6_5_nonresidential_4_25_statutory；test_duplicate_low_original_same_duty_else_5（**命名以 Annex D D11 权威清单为准**；其余 D11 各行不在此重复） |
| 8 | test_final_vs_provisional_separate；test_reduction_not_applied_to_provisional；test_missing_pst_partial_no_total_balance；test_balance_refund_signs；test_consumed_409_reports_existing_record_id |
| 9 | test_decimal_canonical_string_only；test_json_number_boolean_nonfinite_rejected；test_core_determinism_byte_identical；test_rank_server_derived_not_authoritative_input；test_request_response_revision_binding；test_injection_cannot_mutate_rules_amounts |
| 10 | test_missing_not_zero；test_explicit_zero_accepted；test_unknown_triggers_question_list；test_extra_oversize_rejected；test_unsupported_deduction_positive_or_unknown_refuses_tax；test_loss_refund_explicit_fields |
| 11 | test_structured_confirm_before_calc；test_explanation_amounts_from_tool_result_only；test_input_mutation_invalidates_editor_draft_keeps_completed_records；test_unsourced_rate_refused；test_new_tool_recommendation_needs_confirmation；test_confirmation_wrong_target_not_consumed；test_unknown_tool_no_action_strict_params_rejected |
| 12 | test_core_flows_375_and_1280；test_labels_focus_visible；test_states_no_zero_fill_for_unknown；test_ai_suggestion_marked_unconfirmed |
| 13 | test_record_fields_complete；test_json_export_and_print_report；test_replay_requires_pinned_bundle_and_engine_version_exact_equality；test_replay_not_current_reassessment；test_record_excludes_sensitive_defaults；test_audit_log_sqlite_scope_rule_check_publish_rollback_only |
| 14 | test_local_calc_without_model；test_consent_freezes_full_bytes_incl_history_system_tools；test_consent_one_use_consumed_before_send；test_context_change_new_grant；test_keys_server_only_never_browser_log_export；test_model_failure_no_leak_form_preserved；test_clear_session_memory_only_external_prints_kept；test_model_minimal_payload_no_full_export_or_unused_facts；test_stream_read_1mb_accumulation_cap；test_httpx_sends_frozen_bytes_no_reserialization；test_startup_single_worker_no_proxy_headers_loopback |
| 15 | test_fixture_provenance_t1_only；test_no_self_or_reference_oracle；test_red_phase_hand_derivation_documented；test_traceability_all_reqs_covered；test_bad_candidate_parameters_rejected_even_if_engines_agree；test_missing_required_case_quarantines |
| 16 | test_readme_install_run_model_toggle；test_mit_notice_pinned_commit；test_no_ird_endorsement |
| 17 | test_first_startup_due；test_month_end_clamp_31jan_feb28_next_anchor_mar；test_overdue_single_coalesced_catchup；test_manual_same_pipeline_singleflight；test_attempt_vs_success_per_source_global；test_partial_source_no_global_success；test_checked_ok_distinct_from_published；test_manual_reanchor_on_actual_completion；test_no_financial_data_no_llm；test_source_states_enter_clear_and_due_is_not_expired；test_known_change_persisted_before_publish_survives_restart_and_rollback |
| 18 | test_data_only_candidate_frozen_schema；test_semantic_change_to_change_request；test_independent_source_fact_validation；test_candidate_regression_tests_immutable；test_atomic_pointer_no_mixed_versions；test_concurrent_calc_pins_single_bundle；test_rollback_audited_pointer_not_time_travel；test_known_change_or_expired_refuses_affected；test_offline_verified_snapshot_with_notice；test_pa2026_160k_period_quarantine_not_global_140k；test_updater_never_edits_source_tests_specs；test_candidate_gate_isolated_from_current_store_and_network；test_auto_slot_manifest_fields_complete_and_unverified_locator_not_confirmed |

Annex C C10 另行规定新增/替换命名测试（计划，未创建；全部 P0）：精确分数与份额和、canonical 定点格式化器、日期/年度键、子女申索 9 上限、确认三段（needs_input 无 id／consumed 409／LLM 拒绝）、双 epoch（撤回保本地）、快照 pin 与未引用修剪、单实例锁/崩溃中断、consent 确切字节/原子消费/原样发送、Origin null/跨源拒绝、时钟链（含 31May→30Jun→30Jul）、候选验证 vs 独立参考、空报告禁止发布，**以及 REQ-3..7 正向金额覆盖**（按年度/主体成功路径；公式/期望由未来税法 annex 独立 Red 事实治理）；并以行为测试替换了本表原「阻断声明」式命名（见 Annex C C10 末尾对照）。2026-10-07 技术预审整改另增/替换：候选参数/必需 case/门禁隔离三测（C8.1/C8.4/C8.7）、确认错目标不消费、consumed 409 附 record_id、重放引擎版本完全相等（替换 compatible_engine 命名）、HTTPX 冻结字节原样发送（替换 SDK 命名）、最小化外发、未知 tool 无动作、1MB 流式上限、audit 四类边界、启动单 worker 强制回环、auto slot 七字段与未证 locator、guard 落盘、SOURCE enter/clear 与到期≠过期、rank 非权威输入（详见 Annex C C10）。2026-10-08 第二次复审整改另增/调整：C3.5 利得税唯一规范模型（entity_kind/partner_kind/two_tier 事实字段）、C3.7 纳入 E_BUNDLE_INCOMPATIBLE（replay binding 不完全相等拒绝重算）、blocked 六键一律显式 null、哈希对象封闭字段清单（C2.8）、C2.1 整数位 ≤30、manifest 同构（anchors[]/七字段 slots/evidence_only_fields 入 schema）、consent 绑定增 bundle_hash＋test_consent_stale_on_rules_change、C3.6 上游 wire／内部规范消息双层（详见 Annex C C10）。

Annex D D11 另行规定新增/替换命名测试（计划，未创建；全部从税法规格推导，期望值 provenance=T3——基于已核官方事实的独立推导，**可作正式测试期望**（非共享生产 oracle），**非 official fixture**；T1 官方一手 fixture 对账在 Red 阶段固化（caseID＋更多官方例对账），不以缺官方一手 fixture 推迟测试期望）：AVD 三表/Scale 3 与阈值 t±0.01、基数 max 不取整 100、日期制与 s78 谓词；股票 sold/bought 分别 ceil、voluntary 5+0.2%、other $5 不替代买卖税；租约周年日档位（恰 1 年 0.25%／1 年 1 天 0.5%／恰 3 年 0.5%／3 年 1 天 1%）、≤1 年总租金、月租×12、总租金年化 4 位→ceil100→率→ceil1、按金排除；利得税两级制/关联/选择与混合合伙 Q7 612,000、Q8 270,000/54,000/216,000/next PST 375,000（历史例整体保留）；PA 计算器 2025/26 链（rebate ceil cap、share floor、CompTP 单次 floor、PACOut 41/42/43、逐物业 floor）；政策 160k 不激活/不全局 140k。并以行为测试替换本表原 time-gate／端点／历史表 blanket 命名（见 Annex D D11 末尾对照），**负数路径不替代正向金额覆盖**。**D11 为现行权威命名清单；本表 REQ-7 行测试名已按 D11 对齐（2026-10-08），旧 `test_avd_*`／`test_stamp_ord12_s78_preserves_predicates` 等中间命名不再使用。**

## 11. 里程碑与 TDD（红先行，依赖显式）

- 执行方式：每任务 @test-writer 先写命名测试并确认**因预期原因失败**（Red；预期值由来源锚点＋独立手算推导，无共享生产 oracle）；@executor 最小实现（Green）；测试保持通过下重构。不得弱化/跳过/删除测试；范围变更上报编排者。

| 里程碑 | 内容与覆盖 | 依赖 | 代表性 Red 测试 |
| --- | --- | --- | --- |
| M1 基础 | 脚手架；core（money/dates/hashing/errors）；SQLite bundle 存储＋指针；API 骨架＋错误分类法；安全中间件；覆盖数据源；追溯脚本；pytest/Hypothesis/Playwright 接线（REQ-1/2/9/10/14/16 基础面） | 无（绿地） | test_decimal_canonical_string_only；test_missing_not_zero；test_atomic_pointer_no_mixed_versions；test_coverage_page_lists_inventory_with_status |
| M2 并行 | 薪俸税（REQ-3）；利得税（REQ-4）；物业税（REQ-5）三条子线 | M1；法定基础已并入 Annex D D4/D5/D7（T1-现行法例文本，含附表 4 免税额矩阵）；结构/校验/舍入轮廓测试可先 Red；年度数值 fixture 对账按台账政策（T2 候选须一手核对晋升）；利得/物业最终取整待 §12-1 裁定 | test_salaries_mpf_mandatory_deduction；test_partnership_ordinary_business_computable；test_property_q7_example_120000_96000_14400 |
| M4 并行 | 印花税三引擎（REQ-7） | M1；AVD 三表/Scale 3、股票 head 2、租约档位/取整链/premium 冲突/复本 head 4 已法定化（Annex D D1–D3，T1-现行法例文本；普通文书日期即可；s.29/s.29G/s.29D 核心已法定）；多层文书链细分＝范围外 | test_stamp_property_table8_bands_in_window；test_stamp_property_2026_residential_high_tiers_100m_and_109574470；test_lease_exact_1_year_0_25_vs_1y1d_0_5；test_stock_contract_note_sold_and_bought_each_ceil_1 |
| M3 | PA 比较＋缴款拆分（REQ-6/8） | M2 引擎契约冻结；PA 法定基础见 Annex D D6（s.41/42/42A/43/100/附表 1/2/43 全法定；计算器行为 T1-observed 三年度同构） | test_pa_q32_example_9920_vs_28800；test_missing_pst_partial_no_total_balance |
| M5 | 获取/调度/发布/回滚（REQ-17/18） | M2–M4 引擎回归套件存在（作为发布门禁） | test_month_end_clamp_31jan_feb28_next_anchor_mar；test_data_only_candidate_frozen_schema；test_rollback_audited_pointer_not_time_travel |
| M6 | UI 表单/报告（REQ-12/13） | 引擎 API 契约冻结＋Annex A 获批 | test_core_flows_375_and_1280；test_record_fields_complete |
| M7 | AI 集成（REQ-11/14 余项） | M6 | test_consent_freezes_full_bytes_incl_history_system_tools；test_injection_cannot_mutate_rules_amounts |
| M8 | 全套件＋追溯＋@acceptor（REQ-15 终验） | 全部 | test_traceability_all_reqs_covered；test_fixture_provenance_t1_only |

## 12. Gate2 阻断项（以 Annex D D10 为唯一权威；本节为其现行镜像）

**剩余阻断（一项；2026-10-08 按第三轮复审裁定对齐 Annex D D10 与 Annex B 台账）：**

1. **最终取整与物业单位（第五轮复审后口径）**——①**薪俸/PA/非法团利得：三年度官方计算器 floor 观察已闭**（parent T1 直读 st_comp/pac 2024/25–2026/27 原始 JS；rebate cap 1500/3000/0 与附表 43 对齐；汇总件 `cross_year_rounding_evidence.md`）；②**法团/混合主体最终取整＝产品估算精度约定（floor 至整元）**——无适用官方证据（BIR51/52 与 iXBRL「excluding cents」＝申报表填写精度≠评税取整；无计算器覆盖法团分支；整元例不可区分方向），**明确标注「产品约定、未经 IRD 文字证明适用于法团评税」，列为用户 Gate2 确认项**；择档 floor(计税利润)、宽减 ceil 后扣；相反官方证据 → fail-closed（`test_profits_corporate_final_rounding_product_convention_pending_user_gate2`）；③**物业评税/聚合单位已冻结（观察/推导分层）**：观察＝PA 计算器 floor 作用于**输入聚合字段**（`floor(字段AV×0.8)→floor(×15%)`；死分支行号已更正——2026/27 实际 L8725-8726/8840、2024/25 实际 L8570/8677-8678）；推导＝直接物业税按 BIR57 逐项物业评税单位（faq pty.htm Q4「property-by-property」）→ 逐物业 `floor(NAV)→floor(NAV×15%)`；聚合顺序不恒等（反例已录）；PA 移转份额 share=floor(tax×av/tv)（观察）。**两级利得择档与税额为两个不同取整点**（低档分支对**全部 raw 净利**×7.5%；min/max 线性式禁作 oracle，Annex D D6.2a）。官方计算器行为「非普适法定规则」、两条估算器契约不得静默合并（Annex D D7）。

**第三轮复审裁定子项（2026-10-08；不再列为 Gate2 阻断）：**

- **s.12B 已闭**：联合评估单一 NCI 合并口径已由现行法例文本闭合（Annex D D7.0；D10 已闭清单）。
- **s.60＝评税程序**：追加评税属评税程序，首版**不模拟追加评税**；保留 s.43(2B)「追加评税全数归原所得一方」说明引用（Annex D D6.0）。
- **joint PA 首版输出规则**：权威金额＝**宽减后 joint 总税额**；个人份额按 `R_i/ΣR` 保留**精确 Fraction**（内部中间值），**不输出个人整元分摊税额**；`ΣR＝0` → 无税、不分摊。

**范围外注记（不阻断普通门类；Annex D D1.9/D10）**：s.45 关联法团宽免细节、s.29D(3) 以后多协议特殊分部。Ord 8/2024 已并入现行文本（s.78 既有过渡覆盖）、Ord 5/2026 现行整合本无注记（索引有条例 PDF、正文未核）→ 范围外/guard，不以零命中断言其立法事实（Annex B §1.18 追加二、§5；Annex D D1.5/D10）——不再列为阻断。历史闭合过程（B1/B2/B4/B11 与 Cap.112/Cap.117 法定化子项、附表 4 免税额矩阵等）不在此节重复，见 Annex B 台账变更记录与 Annex D D10「已闭」清单。

**恒久注记：**

- **解析依赖 OPEN**：用户已于 2026-10-07 通过问题工具答复「**暂不增加依赖**」——`beautifulsoup4`／`pypdf` 等**未获批准、不得安装**，也不得以其他解析方案绕过同一门禁；解析实现路径保持未决（Annex C C1/C7.3）。
- **技术整改待复审**：R6–R14（技术契约）由 Annex C 承接；2026-10-07 技术预审（仅 R11 文档级闭合）与 **2026-10-08 @plan-reviewer 第二次复审（NEEDS REVISION：R10/R11/R13 文档级闭合）** 均已按意见整改；2026-10-08 **第三轮复审（NEEDS REVISION）**意见已按本轮整改（§13 第六次修订）；2026-10-08 **第四轮复审（NEEDS REVISION，有序清单 8 项）**已全部整改（§13 第七次修订：三年度取整证据闭合、Annex D D4.3/D5.0/D6.2a、D10 拟闭合一项）；2026-10-08 **第五轮复审（NEEDS REVISION，4 项）**已全部整改（§13 第八次修订：法团/混合取整＝产品精度约定待用户 Gate2、物业评税/聚合单位冻结、死分支归因更正、条件公式）；2026-10-08 **第六轮复审：APPROVED**（设计合同可执行性；不证明产品取整约定等同 IRD 实际评税、不授权实施/安装）；**剩用户 Gate2 批准（含法团/混合产品精度约定确认项）**；预审≠全局审批，不构成用户批准。
- **PA2026 提案 guard（不激活）**：官方页明示须经立法、过程未完成（T1）；按相关期间隔离，不激活、不简化为 140k 全局替换；**附表 4 2026/27 子女现值 140k 与 guard 一致**（Annex D D7.0/D8）；不等待立法、不列为 Gate2 阻断项。

强制类别（普通合伙利得/PA、股票普通转让、MPF）不得因剩余 OPEN 子项而整类 unavailable（Annex D D9）；个别未核种子值隔离不构成最终验收豁免。证据接受与 Red 期望政策见 Annex D D10「证据状态」与 Annex B §7.1（基于已核官方事实的独立推导含 T3 算术可作正式 Red 期望，须可审计独立复算）。

## 13. 门禁、状态与变更记录

- 验证归属：编排者（先行）→ @plan-reviewer →（并行 councillor＋@council 汇总）→ 用户 Gate2。本文不声称已评审/已批准；未运行任何测试；未编写任何代码或测试；未执行任何外部抓取（本车道）。
- 2026-10-07：初稿。整合 Annex A/B、已选定架构、REQ-1..18 与命名测试计划、调度/发布状态契约、税务语义闭合表与 Gate2 阻断清单（§12）。
- 2026-10-07（第一次修订）：按 @plan-reviewer 首轮评审（**NEEDS REVISION**，docs/reviews/2026-10-07-sdd-plan-review.md）修订：新增规范性 Annex C 承接 R6–R14（平台基线、金额语法、API/确认/状态、同意状态机、安全会话/单实例、调度时钟重锚、更新数据契约、独立参考 harness）；§10 以实际行为测试替换「阻断声明」式测试；R1–R5 税法语义保持未决。状态：Draft（修订待复审）。
- 2026-10-07（第二次修订，按技术预审整改，待复审）：预审记录 docs/reviews/2026-10-07-technical-pre-review.md（NEEDS REVISION：仅 R11 文档级闭合）。接口以 Annex C 为同一权威：分项金额字段（`before_reduction/…/balance`）、`/api/v1/report/print`（inline＋no-store）、`E_UPDATE_IN_PROGRESS` 202 幂等，替换旧 amounts/`/report/print`/409 表述；AmountStr 语法以 C2.1 为权威（移除宽松正则）；A2 改 Decimal＋Fraction 双轨＋整数商余取整；prepare 返回完整 binding；确认绑定 tax_type/schema_version/input hash/执行目标（错目标 409 不消费）；consumed 409 附同 session record_id；重放＝确认事实＋原 binding 校验重算、引擎版本完全相等；rank 改服务器派生非权威输入（sketch 去 rank）；到期≠过期、guard 状态 classify 后 publish 前落盘；门禁隔离环境（临时 store/无网络/无 session/不写 current pointer）；解析依赖 OPEN（beautifulsoup4/pypdf 仅候选）；启动 `--workers 1 --no-proxy-headers`；audit 四类边界；无 SDK 七段外发管线＋最小化；test-only→Red→Green production 接线顺序。§10/C10 命名测试增补与替换。状态：Draft（整改待 @plan-reviewer 复审）；R1–R5 保持 OPEN；未运行任何测试、未安装依赖。
- 2026-10-08（第三次修订，税法 Annex D 整合，待复审）：新增 Annex D（docs/design/003-hk-tax-legal-contract.md，Draft）承接 R1–R5 公式/资格/事实/取整细化与 OPEN 分层；§3 响应示例修正为 PAM39 Q1 的 T2 候选（before 3,940／宽减 3,000／final 940；缺 PST→partial 且 balance null）；§4 删除 E_TIME_POINT_REQUIRED 的 2025-02-26 示例（现无 time profile；普通文书仅日期）并把 blocked 明确为无金额字段（非税额 0）；§6 各行指向 Annex D D1–D7；§7/REQ-11/REQ-13 明确输入/规则变更仅废当前编辑器流程，完成记录按原 binding 可查/下载/重放（C3.2.4）；§10 删除 time-gate/端点/历史表 blanket 测试并增 Annex D D11 正向行为测试（provenance=T3，非 fixture）；§11 M2–M4 同步；§12 更新为 B1/B2/B4/B11 子项闭合、R1–R5 整体仍 OPEN，并记录用户 2026-10-07「暂不增加依赖」答复。状态：Draft（待复审）；未运行测试、未安装依赖。
- 2026-10-08（第四次修订，按 @plan-reviewer 第二次复审整改技术/契约/主 spec 条款，待复审）：第二次复审（2026-10-08）结论 **NEEDS REVISION**（R10/R11/R13 文档级闭合）。本批整改：§4 `blocked` 金额形态唯一化（六个金额键一律显式 `null`、键不缺省，删「null／缺省」双形态）；§4 摘要与 Annex C C3.7 同步（`E_BUNDLE_INCOMPATIBLE` 409：replay 时 bundle/engine 与原 binding 不完全相等 → 拒绝重算，不降级、不迁移；E_CONSENT_STALE 触发补注）；§6/REQ-15/§10 分层措辞收紧——Gate2 前闭合公式/资格/适用性/取整/独立 oracle 方法，Red 阶段固化边界数值、独立手算工作纸、caseID 与更多官方例对账，基于已核官方事实的独立推导可作正式测试期望（非共享生产 oracle），删除「须先有官方一手 fixture 才可作测试期望」类概括；§8 环境命令改获批解释器 Python 3.12/3.13（`py -3.13 -m venv .venv` 或显式路径＋创建后版本检查；本机默认 3.14.5 超基线、禁用；本批不执行安装）；REQ-4 利得税输入以 Annex C C3.5 **唯一规范模型**为权威（`entity_kind` 三值／`partner_kind`＋利润分享比例／`two_tier` 事实字段；Annex D 由其 lane 同步）；§3 哈希表述同步为「哈希对象封闭字段清单」（Annex C C2.8）。状态：Draft（整改待 @plan-reviewer 再次复审）；R1–R5 保持 OPEN；未运行测试、未安装依赖、不声称评审通过。
- 2026-10-08（第五次修订，与 Annex D 法定化并入同步，待第三轮复审）：法契线已将 Cap.112/Cap.117 现行法例文本（T1-现行法例文本，2026-10-08 版面）与官方计算器行为并入 Annex D，多数 R1–R5 子项闭合。本批同步：§6 导语声明 Gate2 阻断清单以 **Annex D D10 为唯一权威**，各行「Gate2 阻断」列更新为现行状态（薪俸：法定基础已闭、剩宽减-暂缴交互 Red 级；利得/物业：剩最终取整惯例证据；PA：剩分摊余数/取整与 s.60 细节；印花：三表端点/s78/s79/s.29/s.29G/s.29D/premium 冲突/复本全法定）并逐行加「详见 Annex D Dx」指针；§12 重写为镜像 D10（两项剩余阻断：最终取整惯例证据留复审裁定、s.12B/s.60 细节 Red 级；范围外注记 s.45/s.29D(3)；Ord 8/2024 已并入现行文本、Ord 5/2026 零命中＝guard，B3 闭；三项恒久注记：解析依赖 OPEN、技术整改待第三轮复审、PA2026 guard 不激活）；§10 REQ-7 测试名对齐 D11 权威命名（旧 `test_avd_*` 等中间命名退役）；REQ-3/4/5/6/7 措辞指向 D 条款（s.100(3) 每业务宽减、s.5(1A)/s.7C、s.42(1)(a) 但书、附表 43 无物业税行、s.29/s.29G 追问模型）；§11 M2/M3/M4 依赖与 Red 测试名同步；头部状态更新为「R1–R5 子项大幅闭合、待第三轮复审」。状态：Draft（待复审）；R1–R5 整体 OPEN；未运行测试、未安装依赖、不声称评审通过。
- 2026-10-08（第六次修订，按 @plan-reviewer 第三轮复审裁定同步 §6/§12，待第四轮复审）：第三轮复审结论 **NEEDS REVISION**（意见由编排者转达，无独立编号记录）。§6/§12 子项同步：**s.12B 已闭**（Annex D D7.0；D10 已闭清单）；**s.60＝评税程序，首版明确不模拟追加评税**（s.43(2B) 归原所得人说明保留）；**joint PA 首版输出规则**（权威＝宽减后 joint 总额、精确 Fraction、不输出个人整元额、ΣR＝0 无税不分摊）；**最终取整阻断项收窄**为「计算器覆盖子范围之外的年度/分支」。技术契约联动见 Annex C 第六次修订（C3.6 消息协议、consent 四元组、C7.10 anchor_status 枚举）。机械修复：本文件尾部 2551 个与 specs/README.md 尾部 185 个 NUL 字节截尾清理。状态：Draft（待第四轮复审）。
- 2026-10-08（Gate2 批准，Status：Draft→**Approved**）：用户经问题工具明确批准整套 SDD（specs/001＋Annex A/B/C/D），并确认**法团/混合主体最终税额 floor 产品精度约定**（性质：产品约定、未经 IRD 文字证明、报告如实标注、相反官方证据 fail-closed）；解析依赖维持「暂不增加」；同时授权按 Annex C C1 顺序安装依赖（test-only→Red→Green production）。@plan-reviewer 第六轮 APPROVED 在先。进入 TDD（M1 Red 先行）。
- 2026-10-09（最终验收 ACCEPTED，Status：Approved→**Implemented**）：前两轮独立验收 REJECTED 的 5 组阻断＋2 项附加（生产门禁配置、非薪俸表单流程、输入三语义、testgate 实测、证据链/步骤、tax_type 错误形状）全部修复并经第三轮独立验收 **ACCEPTED**；验收由用户指定 **GLM-5.3** 承载（acceptor 代理通道 403 不可用，经只读复核角色执行同等独立验收：全量 157 passed 复跑、追溯 18/18、reference/Scheduler 绑定三场景、浏览器表单流程、digests 实算 hash 逐项 file:line 证据）。`.gitignore` 与 pytest e2e marker 已补。未验证项如实保留（真实外网更新/真实模型请求未执行；仓库尚无提交——未经用户要求不提交）。
- 2026-10-08（第八次修订，按 @plan-reviewer 第五轮复审整改）：第五轮 NEEDS REVISION 4 项全部落盘——①法团/混合主体最终取整：撤回「申报精度＝取整惯例」晋升（BIR51/52＋iXBRL「excluding cents」＝申报表填写精度；复审反例 200,010×8.25%＝16,500.825）；改列**产品估算精度约定**（floor 至整元、择档 floor(计税利润)、宽减 ceil 后扣；标注「未经 IRD 文字证明适用于法团评税」；**用户 Gate2 确认项**；fail-closed guard 保留）；②物业评税/聚合单位冻结：观察（PA 计算器 floor 作用单位＝输入聚合字段；死分支行号更正——2026/27 实际 L8725-8726/8840、2024/25 实际 L8570/8677-8678）与推导（BIR57 逐项物业评税单位→逐物业 floor(NAV)→floor(×15%)；faq pty.htm Q4「property-by-property」）分层标注；聚合顺序不恒等反例入文（两物业 AV 各 17：逐物业 2 vs 聚合 4）；③两级利得公式改条件分支（低档对全部 raw 净利×7.5%；min/max 线性式仅高档分支等价、禁作 oracle——C8.4 逐阶段一致）；④镜像同步（§6 薪俸/利得/物业/PA 行、§12-1、README）。证据件同步修正：cross_year_rounding_evidence.md（聚合单位/死分支/法团处置）、pa_profits_twotier_trace.md（条件公式）。状态：Draft（待第六轮复审）；R1–R5 整体 OPEN；未运行测试、未安装依赖、不声称评审通过。
- 2026-10-08（第七次修订，按 @plan-reviewer 第四轮复审整改＋三年度取整证据闭合，待第五轮复审）：第四轮复审 NEEDS REVISION 有序清单 8 项全部落盘——①附表 3A/3C/3D 误录纠正与资格谓词补全（Annex D D7.0）；②s.7C(3) 以前年度回扣（D5.0；GovHK irrecoverable.htm parent T1）；③普通合伙合同冻结＋分配表口径＝抵亏后份额、Σ＝B、不含亏损（D4.3/D4.4）；④取整/joint 输出（D6.2a/D6.3）；⑤租约 annual 输入模式＋历史 premium 4.25% 期间＋s.10(4)/s.18A 锚（D3.1/D3.2/D3.4）；⑥wire 协议（C3.6）；⑦consent 四元组（C3.2.4/C4.6/C4.8）；⑧镜像/机械同步。**P0 取整年度矩阵闭合**：parent T1 直读三年度官方计算器原始 JS（st_comp 2024/25/2026/27、pa_comp 2024/25/2026/27；汇总件 cross_year_rounding_evidence.md）——薪俸 floor 择径/输出三年度一致（rebate cap 1500/3000/0 与附表 43 对齐）；PA 物业分支 floor(floor(NAV)×15%) 三年度同构；直接物业税（BIR57 Note 3 NAV 定义）与直接利得税（含法团）按「整元表单惯例＋三年度子范围计算器＋无法条」组合冻结 floor 契约，相反证据 fail-closed；trace 择档边界修正（floor(2,000,000.01)=2,000,000→低档，两阶段取整点区分）。§6 利得/PA/物业行、§11 M3、§12 第 1 项、specs/README 索引同步为「拟闭合待第五轮复审确认」。状态：Draft（待第五轮复审）；R1–R5 整体 OPEN；未运行测试、未安装依赖、不声称评审通过。
