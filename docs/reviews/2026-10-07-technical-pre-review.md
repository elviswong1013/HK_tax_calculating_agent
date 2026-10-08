# 2026-10-07 — 技术契约预审记录（Technical Pre-Review）

Status: Review record only（预审记录；**非全局审批**、不批准需求、不授权实现/安装/提交）
Created: 2026-10-07
Updated: 2026-10-07
Reviewer: @plan-reviewer（技术范围预审）
Verdict: NEEDS REVISION（技术预审；**非全局复审结论**）
预审对象: docs/design/002-hk-tax-technical-contract.md（Annex C）＋ specs/001-hk-tax-agent.md 技术条款（R6–R10、R12–R14）
不在范围: R1–R5 税法语义（保持 OPEN）；全局 SDD 审批；UI lane（docs/design/001-hk-tax-ui.md，另一车道独占）

> 定性：本记录仅登记 2026-10-07 的 **plan-reviewer 技术预审**往返：结论 NEEDS REVISION，仅 R11 达**文档级**闭合，R6–R10／R12–R14 均 OPEN。它**不是** @plan-reviewer 全局复审、不是 APPROVED、不改变 SDD／Annex C 的 Draft 状态、不授权任何代码/测试/安装/提交。首轮正式评审记录（docs/reviews/2026-10-07-sdd-plan-review.md，R1–R14 全量）**保持原样**、继续有效，本记录不修改亦不替代它。验证归属：协调者对本轮整改做差异复核 → @plan-reviewer 复审 → 用户 Gate2。整改与本记录同批写入（2026-10-07）；「预审时锚点」为整改前文件行号，「整改后锚点」为现行条款号。**同日第二轮 parent 差异复核整改见 §4**。

## 1. 预审结论总表

| R | 预审结论 | 预审时锚点（整改前） | 本轮整改（整改后锚点） | 复审待核对 |
| --- | --- | --- | --- | --- |
| R6 | OPEN | Annex C C8（261–281 行）；SDD §5 管线 110 行、§10 328–331 行 | C8.1（参数来源/候选隔离）；C8.4（caseID 绑定＋必需 case 全执行＋参数证据＋逐阶段一致＋回归 failed=0）；C8.6 报告增 cases/regression/gate_environment；C8.7（新增，隔离运行门禁）；C10 三测 | caseID「待定不得编造」表述是否可执行；gate_environment 能否作为可断言证据；三命名测试的 REQ 映射 |
| R7 | OPEN | Annex C C2（23–46 行）；SDD A2/§3（30、40 行） | C2.4（整数商余取整；2 位小数仅限 I/O）；C2.8（哈希覆盖 effective 时刻/适用期/冻结语义/证据摘要；排除逐字段列名；core/envelope 边界）；C2.9（rank 服务器派生非权威输入）；C2.10（保存值禁指数记法）；SDD A2 改 Decimal＋Fraction 双轨、AmountStr 以 C2.1 为权威 | 「金额一律 Decimal」矛盾是否消除；是否再无笼统 timestamp 排除；formatter 落盘值无 e/E |
| R8 | OPEN | Annex C C3（48–149 行）；SDD §3 信封 61–77 行、§4 表/错误 79–101 行 | C3.2.1（prepare 完整 binding）；C3.2.2（确认绑定 tax_type/schema_version/input hash/执行目标，错目标不消费）；C3.2.3（consumed 409 附 record_id；错目标 409 不消费）；C3.1（replay＝确认事实＋原 binding 重算、引擎版本完全相等；print inline/下载 attachment）；C3.7 错误表补全＋权威声明；SDD 旧 amounts/print path/updates 409 全部替换为 Annex C 同一接口；状态表增错目标行 | SDD 与 Annex C 是否已无接口漂移；错目标不消费是否有状态表＋错误码双重支撑 |
| R9 | OPEN | Annex C C7（209–259 行） | C7.1（auto slot 七字段；frozen/未知不可写）；C7.2（统一 manifest 可校验 schema；artifact_type 增 html_section；标题/条款 section＋DOM 有界抽取）；C7.3（locator 状态门：未证不可 confirmed；解析库候选未批准）；C7.10 示例与 schema 同步；C1 解析依赖 OPEN（beautifulsoup4/pypdf 仅候选） | 示例是否仍预填未核数值（应无）；未证 locator 门是否可测；依赖候选是否保持未安装/版本许可未声称核实 |
| R10 | OPEN | Annex C C5.6（178 行）、C7.8（233 行）、C7.9（234 行） | C5.6（guard/known-change classify 后 publish 前落盘；重启/回滚不清）；C7.9（SOURCE 各状态 enter/clear 表；到期≠过期）；C7.8（首版无归档被引用快照持续 pin）；C3.2.4（新增，技术历史契约：完成 record 按旧 binding 查/下载/重放；当前 editor/能力/迟到 response 失效） | 落盘时点是否可测；六状态 enter/clear 是否完备闭合；历史契约与「无财务持久化」是否自洽 |
| R11 | closed（文档级） | Annex C C6（189–207 行） | 无整改（本轮未动 C6 语义） | 复审确认 C6 与 C8.7 隔离运行环境表述无冲突即可 |
| R12 | OPEN | Annex C C4（151–169 行）、C3.6（130–132 行）；SDD §7（156–158 行） | C4.1（wire profile＋HTTPX content=frozen_bytes，无 SDK）；C4.6（七段外发管线：白名单→最小化脱敏→一次 serialize→完整预览→授权→原子 consume→原样发送；默认不发送完整 export/全部确认 facts/未用信息）；C3.6（模型响应/工具 schema、strict 参数、未知 tool 无动作、批次限额 ≤4/条；精简现注册表不造新功能）；C4.4（1MB＝stream-read 累积上限；每次外发独立授权）；SDD §7 同步 | 「SDK」表述是否清零；预览字节=发送字节是否单路径可保证；最小化默认是否有白名单支撑 |
| R13 | OPEN | Annex C C5（171–187 行）；SDD §7–§8（153–173 行） | C5.1（启动强制 `--workers 1 --no-proxy-headers`；非回环拒绝启动）；C5.8（新增：SQLite audit 仅 rule/check/publish/rollback 四类，无 session/consent/capability/财务 payload；会话/同意事件仅内存）；C5.7（打印 inline＋no-store／下载 attachment＋no-store 分行）；C6.2（reanchor 归 check 类）；SDD §7/§8 命令同步 | 启动参数是否同时出现在契约与命令/测试计划；audit 四类边界是否与 C6 一致 |
| R14 | OPEN | Annex C C1（12–21 行）、C11（339–341 行） | C1（Gate2＋授权安装 → test-only pyproject/[dev]/env → 第一 Red → Green 加 production 依赖；TestClient 生命周期或显式 ASGI lifespan；假件 clock/outbound/tmpDB/legalhost 进程内实现、不增第三方库）；C11（索引记录三项待决） | 依赖安装顺序是否阻止提前引入 production 依赖；「不增库」假件清单是否完备 |

## 2. 预审要点与处置依据（按 R）

### R6 — 独立参考 harness 与发布验证
- 发现：独立参考参数与候选参数对象/生产税务·取整 helper 的隔离未成文；profile/期间与必需 case 集合无绑定，存在临场造 case 风险；门禁运行环境未限定（可能触网、依赖用户 session、写入 current pointer）；缺坏候选参数/缺 case/门禁隔离三類行为测试。
- 处置：按预审意见收紧 C8.1/C8.4，新增 C8.7；caseID 集合显式标注「由税法 annex 独立 Red 事实固化，未定不得编造」；C10 增 `test_bad_candidate_parameters_rejected_even_if_engines_agree`、`test_missing_required_case_quarantines`、`test_candidate_gate_isolated_from_current_store_and_network`（REQ-15/18/13）。
- 待复核：三测试与 C8.6 报告字段（cases/gate_environment）能否一一对应；「双引擎一致不作证据」是否覆盖 harness 与候选共用坏参数的情形。

### R7 — 金额/标量语法与哈希
- 发现：SDD「金额一律 Decimal」与 Fraction 有理中间值矛盾；bundle hash 排除「时间戳」属笼统类别（覆盖不足风险）；hash 未显式覆盖 effective 时刻/适用期/冻结语义/证据摘要；core 确定性与外层 envelope 边界不清；formatter 保存值的指数记法未禁；rank 作为输入字段与「派生」矛盾。
- 处置：C2.4/C2.8/C2.9/C2.10 按上述收紧；SDD A2 改双轨表述、AmountStr 内联宽松正则删除（以 C2.1 为权威）；C10 哈希行改为覆盖＋逐字段排除＋core 边界三测，并增 rank 测试。
- 待复核：逐字段排除清单是否足以复现 hash；「2 位小数仅限 I/O」与步骤记录 num/den 表示是否一致。

### R8 — API/状态/确认与统一错误
- 发现：SDD 旧 amounts 字段名/`POST /report/print`/`E_UPDATE_IN_PROGRESS 409` 与 Annex C 接口漂移；prepare 未返回完整 binding；确认能力未绑定 tax_type/schema_version/执行目标（错目标可消费的漏洞）；consumed 409 未契约化 record_id；replay 信任导出税额且「兼容引擎」未定义为完全相等；错误表不完整。
- 处置：SDD 三处旧表述替换为 Annex C 同一接口；C3.2.1/C3.2.2/C3.2.3/C3.1/C3.7 按上述收紧；C10 增错目标不消费与 consumed 附 record_id 两测，重放测试改名为引擎版本完全相等。
- 待复核：执行目标绑定的失效交互（TTL/输入变更/epoch）是否完备；SDD §4 与 C3.7 是否逐码一致。

### R9 — 更新数据契约/manifest
- 发现：artifact_type 缺 html_section；无统一可校验 manifest schema；auto slot 缺 profileID/fullpath/type/unit/constraints/anchor/proof_role 七字段；示例与 schema 不同步且 locator 预标 confirmed；HTML/PDF 解析库未决但无显式 OPEN gate。
- 处置：C7.1/C7.2/C7.3/C7.10 按上述收紧；示例 locator_status 改 `unverified_pending_dom_check` 并声明不得预填未核数值；C1 增解析依赖 OPEN 行（beautifulsoup4/pypdf 仅建议候选、未批准不安装、版本/许可未核实）。
- 待复核：七字段在 C7.5 已知源台账中的落地方式；OPEN gate 是否足以阻止提前引入解析依赖。

### R10 — 证据 pin/来源状态/持久与竞争
- 发现：known-change/guard 状态的持久化时点未定义（classify 与 publish 之间是否落盘）、重启恢复/回滚语义不完整；SOURCE 六状态无 enter/clear 条件，「到期」与「过期」存在混用风险；首版尚无受控归档时被引用快照的 pin 未写明；完成 record 按旧 binding 的查/下载/重放历史契约未成文。
- 处置：C5.6 增落盘时点与重启/回滚语义；C7.9 改 enter/clear 表并声明到期≠过期；C7.8 增首版持续 pin；新增 C3.2.4 技术历史契约（SDD §3 同步）；C10 增 guard 落盘与 SOURCE 状态两测。
- 待复核：落盘时点与 §5 管线阶段 3→7 的映射；历史契约与「无财务会话表」的边界表述。

### R11 — 调度时钟（文档级闭合）
- 发现：无（重锚链 31Jan→28Feb→28Mar、闰年 29Feb→29Mar、隔离完成检查不发布、退避无忙循环及命名时钟测试齐备，符合 PRD AC-17）。
- 待复核：仅确认 C6 与新增 C8.7（门禁运行环境）无语义冲突。

### R12 — 模型 profile 与同意
- 发现：C4.6「SDK 以 content=bytes 原样发送」与「不得重新序列化」并存造成 SDK 依赖歧义；外发管线各段（白名单/脱敏/单次序列化/预览/授权/消费/发送）未成文；默认外发内容最小化（不发送完整 export/全部确认 facts/未用信息）未写；模型响应 schema、工具 schema、strict 参数、未知 tool 行为、批次限额未定义；1MB 未说明为流式累积上限。
- 处置：C4.1/C4.4/C4.6/C3.6 按上述收紧（注册表保持四工具，不新增功能）；C10 将 SDK 命名测试替换为 HTTPX 冻结字节测试，并增最小化/未知 tool/1MB 三测；SDD §7 同步。
- 待复核：单次 serialize 是否能被测试断言（预览字节=发送字节）；批次限额 ≤4/条是否随评审确认。

### R13 — 安全/会话/持久化边界
- 发现：启动参数（单 worker、no-proxy-headers）与强制回环未成文；SQLite audit 与内存事件的边界未划（存在 session/consent/财务 payload 入库风险）；打印与下载的 Content-Disposition 未区分。
- 处置：C5.1/C5.7/C5.8 按上述收紧；C6.2 reanchor 事件归 check 类以保持四类边界自洽；SDD §7/§8 同步；C10 增启动回环与 audit 范围两测。
- 待复核：audit 四类边界与 C6 调度事件、AC-13 记录下载（会话内存态）是否自洽。

### R14 — 平台基线与 Gate2 前置
- 发现：依赖固化时机未区分 test-only 先行与 production 后置（存在提前引入运行时依赖风险）；API 测试生命周期（TestClient/ASGI lifespan）未指定；假件策略未声明「不增库」；索引未记录技术整改/解析依赖/税法三项待决状态。
- 处置：C1 固化时机改为 Gate2＋授权安装 → test-only → 第一 Red → Green production；测试接线补生命周期与进程内假件清单；C11 补索引记录义务；specs/README.md 索引行同步三项待决。
- 待复核：第一 Red 时仓库内应仅有 test-only 依赖声明的可检验性；假件清单是否遗漏（如假文件系统）。

## 3. 门禁与状态

- 预审后状态：SDD 与 Annex C 均 **Draft**；本文与其余整改文件同批写入（2026-10-07）；未运行任何测试、未安装任何依赖、未编写任何生产代码、未提交。
- 仍阻断：① R1–R5 税法语义 OPEN（首轮评审 1–8 项不变）；② HTML/PDF 解析依赖：用户已答复**「暂不增加依赖」**（2026-10-07，处置见 §4-1）——不批准 beautifulsoup4/pypdf、不安装，选型保持 OPEN gate；③ 本轮整改（含 §4 第二轮）待协调者差异复核＋@plan-reviewer 复审；④ 复审 APPROVED 后仍需用户 Gate2 才能进入 TDD；⑤ specs/README.md 索引与 SDD §12 item 12 的「解析依赖待用户批准」字样已因用户答复过期，归 specs lane 修正（技术 lane 本轮无该写权限）。
- 本记录为技术预审记录，不是全局审批；首轮评审记录（docs/reviews/2026-10-07-sdd-plan-review.md）保持原样并存，对 R1–R14 的门禁效力不变。

## 4. 第二轮整改追加（2026-10-07，parent 差异复核后；仍 Draft／待 @plan-reviewer 复审）

| # | parent 指出 | 本轮处置（整改后锚点） | 复审待核对 |
| --- | --- | --- | --- |
| 1 | 用户已明确答复「暂不增加依赖」（2026-10-07）；C1/C7.3/C11 的「待批准」暗示用户未答，错误 | C1 行改「用户已答复」：不批准 beautifulsoup4/pypdf、不增加/安装任何解析依赖；C7.3 同步；C11 索引义务改「已答复暂不增加」。选型 OPEN gate，**不得以其他方案变相绕过用户决定**，其余文档先行 | 三处措辞是否一致且无「暗示未答」残留；specs lane 的过期字样（见 §3-⑤）是否跟进 |
| 2 | C3.2.4/C5.4「保已完成 record」与「报告失效」表述模糊 | C3.2.4 重写为**四行触发矩阵**（input_revision/rules/consent_epoch/session_epoch 各行互斥；失效机制即 confirm/consent 绑定字段变化）；C5.4 与 C3.1 session DELETE 行对齐：session_epoch 清空/过期**删除**会话内存 records；已完成 record 的原 binding 查/下载/原 snapshot 重算以**会话存续**为前提 | 四触发是否互斥完备；「会话存续」前提与 A3 无财务持久化是否自洽；迟到 response 归属行 |
| 3 | blocked 状态需明确「不输出金额≠税额 0」 | C3.3/C3.4：blocked＝`amounts` 全 `null`（不填 0）＋`blocked_reason`＋统一错误形状（C3.7） | 与 partial「部分已知金额」对照是否无歧义；UI 呈现由 Annex A lane 同步（本 lane 不写 UI） |
| 4 | 主 spec 金额例 `before_reduction: 940` 有误（应为 3940/3000/940），legalwriter 修主 spec；Annex C 若有同例一并核对，没证据别添数字 | 核对结果：Annex C **无**该金额例（C3.4/C8.6 等均无税额数值）；唯一「940」为 C2 判定示例的负数语法例 `"-940"`（退款/亏损字段形态示意，非税额断言）——保留不改，亦未新增任何无证据数字 | legalwriter 修主 spec 后，Annex C 是否仍无需联动 |
| 5 | `E_TIME_POINT_REQUIRED` 的「2025-02-26 11:00 切点」类示例应删：oracle 读 2025 Ord 12 s.78 为 before-day 而 Table 9 自 26 Feb 起；metadata commencement 11:00 ≠ 签署时分 gate | C2.8 删「含 11:00 类切点」表述，改「以税法 annex 核实条款为准，不得以 metadata commencement 时刻推断签署时分门控」；C3.5 `instrument_time?` 改条件必填；C3.7 增注：触发以逐条款核实为准、不预设切点示例。主 spec 同类示例由 legalwriter 负责（本 lane 无写权限） | Annex C 是否已无 11:00 门控含义残留（关键检索见本轮报告） |
| 6 | C3.5 印花税笼统字段（consideration_or_value/link_facts?）应指向新领域模型，不自行立法 | C3.5 印花税行改指向 **Annex D**（`docs/design/003-hk-tax-legal-contract.md`，Draft，legalwriter lane 独占）：property 两金额+max 关系 predicate、stock 每票据 inventory、lease 真实 term/rent/premium/duplicate；表后注「不自行立法」 | Annex D 创建后字段名是否需回填本文（当前仅指针，不预填语义） |
| 7 | 保留技术写稿与预审复审待状态记录；不修改首轮正式 review；C8 caseID 待法律闭合不猜 | 本节即追加记录；首轮 review 未触碰；C8.4「税法未定→集合待定，不得编造」原样保留 | 无 |
