# Test Fix Board — stage1 P / Red质量定点纠错（2026-10-15）

Status: D4-P闭合（2026-10-16：D4=1用户授权第三轮点修→exe-71交付→ora-21独立复审PASS：PQA-01/02均RESOLVED、质量门PASS；PQA-05已PASS；P板六项全部质量闭合；P未冻结、不授权Green/M0）
Spec: `specs/002-tax-correctness-remediation.md`、Approved005/006；源根因及预算仍以 `2026-10-15-tax-stage1-p1-r2.md` 为准。

## 基线、历史与验证口径

- 仅COPY六个P测试文件；MAIN仅协调材料。绝不改app/spec/conftest/ABC/依赖，不新增测试生产hook。生产例外12项均0/1，额外最终P1 gate未使用。
- 原P158质量库存：`p1-r2-red-p-review.md`（16项，第一次NEEDS REVISION）。已做一次定点纠错P162，第二次审阅 `p1-r2-red-p-review2.md` 仍拒绝。更早95/64/158构造历史保留，不凭此板重置历史；两次拒绝触发**先咨询**。
- 既有完整单批执行证据：162 nodes/79F/83P/0 error/skip，旧158 common状态不变；不是162个合法案例均被接受。六hash/AST/71app集合和hash已由协调员核验。不为建板重复运行现有不合格fixture。
- 精确命令/唯一Temp证据：`p1-r2-red-protocol-corrections.md:16–30,112–115`；JUnit在Temp/opencode/hktax-r2-p-corrections-7fdc46e7742cac52/junit-batch-final-c4a1.xml。旧报告不可覆盖，其“16项闭合”声明已被review2否定。
- 本板治理对象是**测试质量缺陷**，不是通过修改期待压低预期产品Red数量。基线：5类未闭质量缺陷+1项新CF1消费对齐。里程碑收敛看未闭质量项减少、无非目标回归、精确首失败归因；合法新增产品Red允许如实增加，不能为数量求绿。真实接口未交付只记接口held，不冒充行为证明。
- 每项最多2次点修；下面1/2是从P158库存后已发生的P162纠错，不虚构更早逐项次数。第二次仍未闭即BLOCKED，不第三试、不换owner重置。CF1是获批新澄清消费项，0/2。所有下一次尝试须先写不同假说和范围；咨询不消耗源预算。

## 文件边界（相对COPY）

`tests/api/test_002_schema_versions.py`、`test_002_salaries_couples_api.py`、`test_002_confirmations.py`、`test_002_assessment_dependencies.py`；`tests/engines/test_002_consume_semantics.py`；`tests/unit/test_002_store_compat.py`。

## Items（全为审阅及Approved合同确认的test-expectation/fixture缺陷）

### PQA-01 安全守卫生命周期与实际DB观察
- Location: T:445–491,553–555,623–626；C:591–600；U:613–622；其余P文件同构。
- Symptom: collection永久setattr污染同进程；env恢复后检查可跳过，inline app实际store未观察。
- Class: test-expectation（验证夹具；不是已观察到真实出站）。
- Attempt1 hypothesis: preimport真实class/transport守卫+事后gate可覆盖所有实例；结果只解决真实出口，未闭安装/复原作用域和store路径。
- Attempt2 candidate hypothesis: 文件内有界pre-main fixture保存并finally复原真实方法；在app构造时登记实际store路径，独立finally保证counter与DB两类检查均执行。具体顺序待oracle咨询，不创建shared conftest。
- Suspect files: 六文件的安全安装/fixture/gate，只有机械一致性调整。
- Acceptance: main前守卫、隐式+显式app/真实captured出口、真实store均可观察且Temp；不拦ASGITransport；最后恢复原方法，允许后续legacy Fake正常运行；失败路径同样执行检查，不以env缺失/计数失败跳过其它检查。
- Attempts: 2/2；Status: BLOCKED。最终审阅：init前用未规范化路径，connect及init-done仅限batch而非当前函数新store；构造后检查不补成I/O前防护。未观察到实际越界I/O；不得据正常样本声称路径边界完备。
- D1授权1次（W-P，2026-10-16已用）；终局FAIL（ora-15）：三原缺口已闭（resolve前置判定/身份准入/检查收窄），但安装内部失败路径 T:546–548 先 _restore_own 保存并清栈（T:491–512）、fixture finally T:1040–1044 二次调用覆盖 snapshot 为空 → T:859–874 identity 循环退化为空检查；Scope C（D:238–242）在 install 成功返回后注错未覆盖该分支。静态推论非已观察复原失败。携入M0须用户另行授权。详见 STATE/p1-r2-wp-quality-review.md。 **D4-P（2026-10-16，用户D4=1授权第三轮）**：exe-71 六文件 extend() 累计快照（schema_versions:638–639、salaries_couples_api:451–452、confirmations:480–481、assessment_dependencies:487–488、consume_semantics:496–497、store_compat:502–503）；双重恢复不再缩减；F1 install内部注错快照6项+最终identity篡改检出；ora-21复审 **RESOLVED/质量门PASS**（STATE同文D4-P节）。

### PQA-02 A4真实producer身份，不注入影子key
- Location: C:1933–1934,1974–1976；app/api/routes.py:149–159、web/security.py:195,318–378只读。
- Symptom: test新增schema_version后get回同值不证明真实身份权威，旧A4a为假行为Red。
- Class: test-expectation。
- Attempt1 hypothesis: stored prepared中get回2.0.1即可认定真实input-schema producer；review否定，真实producer当前没有该字段。
- Attempt2 candidate hypothesis: 从实际producer负载发现已有输入schema承载并先核合法值，缺失则明确接口首失败；仅改真实既有字段，409 STALE、不签能力/不消费/engine0；恢复真实producer后NEW prepare+confirm。支持合同合法schema_version/input_schema_version，不冻结额外字段名。
- Suspect files: confirmations。
- Acceptance: 不新增假字段、不伪造能力；A4a行为声明撤回，实际首失败合法；A4b和NEW恢复保留；内部error.field不冻结。
- Attempts: 2/2；Status: BLOCKED。当前真实无carrier改为接口Red有效；后置A4a缺NEW prepare+confirm恢复，非Mapping未比对已有stored carrier，任意_replace重建依据不足；不是生产接口未交付本身导致拒绝。
- D1授权1次（W-P，2026-10-16已用）；终局FAIL（ora-15）：Mapping/dataclass比对/不插入字段/删任意_replace/A4a NEW恢复已闭；但 _authoritative_material_same C:2407–2408 接受任何同类型tuple、未限标准 _fields 真实namedtuple（(("x",),("x",))→True应为None），与报告声明不一致；诊断D:140–142未覆盖该比较分支。接口held定性合法非拒收原因。携入M0须用户另行授权。详见 STATE/p1-r2-wp-quality-review.md。 **D4-P（2026-10-16，用户D4=1授权第三轮）**：confirmations:2411–2426 tuple准入判别（两侧_fields均tuple且全str，不满足→None；(("x",),("x",))→None）；F2 12控制；ora-21复审 **RESOLVED/质量门PASS**。

### PQA-03 C11权威观察不可把不可见填成零
- Location: T:2210–2212；C:700–702。
- Symptom: `or {}`把缺session/executions/records当空集合，产生假零增量。
- Class: test-expectation。
- Attempt1 hypothesis: 锁内identity-set比较足够；结果观察面不存在时仍可假通过。
- Attempt2 candidate hypothesis: 锁内先断言真实session及权威集合存在可读，再取身份集合；不可观察则失败/held，不约束entry表示，不新增getter。
- Suspect files: consume_semantics、confirmations。
- Acceptance: 13 keyword-only/strictbytes及旧C11诊断保持；零必须是真实观察；helper返回值实际使用/所有末次复制预commit仍列未来源码审计，不抢称通过。
- Attempts: 2/2；Status: PASS（测试质量）。锁内先核sid及真实集合再取frozenset，无假零回退；真实返回值数据流/末次复制预commit仍属合法held，不冒称运行通过。

### PQA-04 already_stored完整Receipt遗漏editor_result
- Location: T:3042–3050。
- Symptom: epoch/revision已补，未检查already_stored.editor_result。
- Class: test-expectation。
- Attempt1 hypothesis: epoch/revision+clear分支足够；结果完整Receipt少一字段。
- Attempt2 candidate hypothesis: 无其它变化的同core重交直接核editor_result=current，不再建矩阵。
- Suspect files: consume_semantics。
- Acceptance: 已有完整Receipt其它字段、同core/异core/clear优先保持；新字段断言不被称当前已运行通过（当前签名仍接口held）。
- Attempts: 2/2；Status: PASS（测试质量）。无其它变化的已有案例补current，其它Receipt保证保留；completion签名仍接口held，未称新断言已运行通过。

### PQA-05 object表示的完整逻辑封闭字段
- Location: T:1145–1189,2124–2129及G14调用。
- Symptom: object分支required/forbidden不能排除任意额外逻辑字段。
- Class: test-expectation。
- Attempt1 hypothesis: Mapping精确集合、object required/forbidden兼容即可；结果object封闭性不成立。
- Attempt2 candidate hypothesis: 仅通过已支持的实际表示枚举逻辑字段全集再精确比较；无法枚举则held，不能要求dict、把方法/实现属性当逻辑字段或发明DTO协议。具体枚举策略待咨询。
- Suspect files: consume_semantics；confirmations只在同helper确受影响时机械同步。
- Acceptance: Mapping和合法object不被误拒；任意额外逻辑字段即使None也拒；五税inputschema=1.0.0保持；opaque不可观察不是已证明封闭。
- Attempts: 2/2；Status: BLOCKED。普通带_fields字符串tuple的对象被当真实namedtuple；控制_Named并非tuple且无year/status数据字段仍被记通过，不能证明封闭观察。
- D1授权1次（W-P，2026-10-16已用）；**质量PASS（ora-15复审）**：T:1613–1624 tuple前置判别闭合伪namedtuple缺口；_Named→HELD控制有效；Mapping/dataclass增量保留；G14仍在availability_context（T:4627–4630）接口held不冒充行为通过。Mapping/dataclass有效增量保留。

### PQA-06 CF1现有唯一错误节点完整field
- Location: S:1022–1030,1040,1045,1055,1069,1118。
- Symptom: 新获批统一field路径，现有consumer仅code断言未对齐。
- Class: test-expectation（授权新增，不计旧16未兑现）。
- Attempt1 candidate hypothesis: 对现有单错fixture显式增加独立literal完整路径，不以HTTP响应当oracle；不扩复制C矩阵。
- Suspect files: schema_versions。
- Acceptance: surprise/bonus/birth_date/participant person_id/rootpresence days按review2精确路径；不误改questions.field、信封schema_version或内部input.g22；合法manual=[]不批量改。
- Attempts: 1/2；Status: PASS（首次consumer测试质量）。五个独立完整literal已对齐；唯一旧pass→fail为Approved CF1产品Red，不发明多错优先级或扩写其它错误域。

## Milestone / validation owners

- 咨询已终结：`p1-r2-red-p-qa-consultation.md`（ora-14）；协调员采纳函数级独立恢复栈/真实store观察、实际producer缺字段接口held、有限逻辑record枚举。以下派发以该文为精确处方；不再等待税法/产品策略澄清。
- M-PQA：exe-64最后有界点修已终结，报告 `p1-r2-red-protocol-corrections2.md`；父级metadata见red-pqa2-reconciliation；pla-12最终独立质量NEEDS REVISION全文及裁定见 `p1-r2-red-p-qa-review-final.md`。旧报告不覆盖，作者“六项闭合”不采纳，无后续P写授权。
- 作者：先各项最小观察/控制，再仅六P文件完整单批；MAIN venv、cwd COPY、唯一TempDB在main前、无lifespan/服务/网络、唯一basetemp/log/JUnit。文件级fixture验证必须独立证明安装/复原；必要时Temp只读诊断脚本，不保留生产hook。
- 协调员：全6full SHA/AST、71app文件集/hash、node-status及已有节点去留/首失败差异、report与源范围。新增field导致合法产品Red须单独解释，旧无关状态不得退步。
- 独立plan-reviewer：只核本板6项实质及未达声明；这是M0测试质量审计，不消耗最终P1 gate。每项attempt2未闭就BLOCKED。
- M0依然依赖A/B/C合法Red及安全冻结；B生命周期恢复问题不由此板绕过。全目录/legacy/e2e仍不运行。

## Discovered during fixing

- 无新生产缺陷擅自加入；新发现先登记并分流，不追修。oracle咨询若发现方案不可行，记录BLOCKED/待裁定，不据此扩源码或更多fixture重构。
- 最后复核结束本轮P-QA：3 BLOCKED / 3质量PASS。162 nodes/80F82P/0error0skip、71app不变仅为元数据；不改变预算终局。不得第三点修、换owner重置或用源码例外预算掩盖测试质量失败。
- **D4-P收官（2026-10-16，用户D4=1明示授权第三轮）**：PQA-01/02各一次新点修（exe-71）→ora-21独立复审 **PASS**（PQA-01/02 RESOLVED）。六文件终态041c9dce/713d98be/5c218b81/1141b158/ee71a624/4c650173；§2全量对比CLEAN（仅六P变化/增删0/app0）；单批162/80F/82P/0E/0S与W-P全等（added/removed/status_changed=0）；非阻断勘误2条见STATE（报告计数笔误、消息分析证据边界）。**P板六项全部质量闭合**。
