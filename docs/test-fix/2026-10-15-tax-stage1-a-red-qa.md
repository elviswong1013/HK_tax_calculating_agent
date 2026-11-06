# Test Fix Board — stage1 A / Red质量有限库存（2026-10-15）

Status: A全闭合（2026-10-16：D4-A→AQA-02/05/06 RESOLVED；A2=1单点修→AQA-08 RESOLVED（ora-25）；八项全部质量闭合；A未冻结、不放行Green/M0）
Spec: `specs/002-tax-correctness-remediation.md`、Approved005/006；源码根因预算仍见`2026-10-15-tax-stage1-p1-r2.md`。

## 基线／范围／停止规则

- 原A92/23F69P → 一次整体有界点修候选111/37F74P/0error0skip；run1同111/38F73P，R结构补断言撤回后最终37F，不把少一fail当质量收敛。两次质量NEEDS REVISION见`p1-r2-red-a-review.md`及`p1-r2-red-a-review2.md`。
- 执行库存为既有`Temp/opencode/hktax-r2-red-lane-a-corrections-20261015-224440-59f609d6/junit.xml`、first-failures及focused工件；命令见作者corrections报告45–46。本板不重跑，不把37fail统称37产品缺陷。
- COPY仅PA=`tests/engines/test_002_pa_allowances.py`、C=`tests/engines/test_002_salaries_couples.py`、R=`tests/engines/test_002_result_semantics.py`、M=`tests/engines/test_personal_assessment.py`；当前四文件均无写权。MAIN仅协调记录。不动app/spec/conftest/其它lane/依赖/网络/服务/git。
- 原八项编号不变；1/4质量PASS且不重开，2/3/5/6/7/8为有限未闭项。交叉的PA married decision记原2/8关联，不另建0次；新增遗漏子项不重置其原根因。
- 每根因最多2次点修；一次整体任务、多个工具调用、多次运行、失败反馈后新方案是不同单位。内部逐项历史待核，不能默认1/2或已证全2/2。未证余量即UNKNOWN-BLOCKED；已证两次即终局BLOCKED，不第三试/换owner/借source例外重置。
- 连续两次质量拒绝先咨询；任何可能的下一尝试还须合法余量、明确范围/不同假说/验收。当前只有只读咨询和历史补证，不派writer。源12根因0/1与唯一额外P1 gate未用；M0/Green/P2均未放行。

## AQA-01 / 原1 — PASS（仅质量）

- PA1360–1427：100/100缺next改unresolved有Approved依据；minor+181/三年100独立，年龄OR Red保留。已闭，不重开或强求旧错期待。
- 过程次数不凭节点F→P重置；无新修改授权。

## AQA-02 / 原2 高 — 金额与trace独立、PA married decision

- Location: PA1835–1862,1903–1921,2006–2032,2070–2094。
- Class: test-expectation（覆盖独立性与缺失trace对账），非全部金额产品缺陷。
- Symptom: 金额已实达，但仍聚合金额/basis/decision；excluded与raw-ineligible 58500/55500正确，却被旧报告统归金额Red；PA married grant/归属/单位缺。
- Prior hypothesis: 一次聚合观测可替代独立金额节点；复评否定独立可选/单一目标。
- Candidate（未授权）: 现有金额单独节点，basis/reduced/decision独立；同已批例补PA married grant/归属/原单位对账，不借salary替代。
- Acceptance: 保留19060/16060、41500/38500、58500/55500，正确金额非Red；trace缺陷与金额分开，不增生产修复/新政策。
- Attempts: 2/2（W0核账：P8–11金额先行→focused反馈→P24聚合，两周期；见p1-r2-budget-adjudication.md）。Status: BLOCKED（终局；D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；终局FAIL（ora-16）：married金额/basis/decision/ERCE100k已独立（PA:1879–2073）；但excluded/raw-ineligible仍同节点串行（PA:2076–2151，首败2100/2138后decision检查未执行）；「金额节点只读amounts」不准确（PA:1890–1891）。携入M0须另行授权。详见STATE/p1-r2-wd3a-quality-review.md。 **D4-A（2026-10-16，D4=1第三轮）**：PA:2078–2179 拆为六独立节点（amounts/reduced basis/decision各自独立首失败）；PA:1893–1904 married 金额节点仅访问 amounts；ora-22复审 **RESOLVED/质量闭合**。

## AQA-03 / 原3 高 — bID/hID独立case及合法控制

- Location: PA1259–1280,2729–2753；已闭拆分PA2812–2840不重开。
- Class: test-expectation（独立性/合法对照）。
- Symptom: 3候选与12组合确实调用，不能继续称未达；仍单node循环，无逐候选控制，非AppError可截断余项。
- Prior hypothesis: 聚合收集所有AppError即可等价独立case；复评否定。
- Candidate（未授权）: 固定3/12展开参数节点；同输入先合法控制，仅改目标p2原值；pa_self可合法mixed-unresolved。
- Acceptance: 每子例可独选、不依赖其它候选成功；精确CONTRADICTORY不改；原金额3与单位1拆分两个目标保留。
- Attempts: UNKNOWN/2（W0核账：可见仅P4–6一周期且各未再改；无平台级完整性交叉核验，作者自述不足以排除第二周期）。Status: UNKNOWN-BLOCKED（D3已裁定=每项一次新点修，W-D3授权1次；见p1-r2-budget-adjudication.md）。
- W-D3授权1次（2026-10-16已用）；**质量PASS（ora-16）**：bID3/hID12独立参数节点、先合法控制后p2单变异、精确CONTRADICTORY、mixed控制限unresolved+CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED、金额3/单位1保留；15控制实达。

## AQA-04 / 原4 — PASS（仅质量）

- PA1062–1082,2852–2855/C643–645,1973–1993的局部actual history/assessed/no_pa/elected与同输入issue.id单变异已接受；不扩共享默认，不宣称全部E1/E4验收。
- 无新修改授权。

## AQA-05 / 原5 阻断 — 状态期待、actual合法模式及未达

- Location: PA2456,2500–2523,2566；C1397,1430–1436,1477,1575。
- Class: test-expectation（错误状态/非法前置/遮蔽），不是新政策歧义。
- Symptom: PA history第二轮及question未达；partial容忍无依据；hypothetical两候选已有separate38500正确partial却强求needs_input；actual elected=true选separate不合法；ordinary/named控制未执行。
- Prior hypothesis: needs_input统一优先或容忍集合足够；被F005621–623具体scenario合同否定。
- Candidate（未授权）: actual相关有限矩阵只请求/选joint；hypothetical保留两候选核partial/knownseparate/bestnull/topfinalnull；history单pa_self无已知scenario需needs_input。各状态参数化，定位独立观察，先同输入完整控制再唯一变异。
- Acceptance: 不以容忍集/正确partial算产品Red；保留原有限依赖范围与缺失/unknown差异，不新冻结question全文或未知path；报告标明未达。
- Attempts: 2/2（W0核账：P12–17/C4/C8→focused反馈→P22/P23/C9聚合，两周期）。Status: BLOCKED（终局；D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；终局FAIL（ora-16）：四组仍循环/聚合（PA:2565–2607/2643–2700、C:1454–1508/1649–1721）违逐状态独立节点处方；PA后轮未执行（首败2590/2675后未达）；hypothetical未断言独立separate 38500保留（C:1470–1495）；s10b定位仅搜"nomination"未分actual/hypothetical（C:1478）。四态needs_input收紧/actual仅请求选joint已满足。 **D4-A（2026-10-16，D4=1第三轮）**：PA:2600–2791 history/assessed 各四节点；C:1506–1680 s10b 八节点（含 hypothetical 38500 双观察、basis 精确区分）；C:1805 起 joint actual 八节点；ora-22复审 **RESOLVED/质量闭合**。

## AQA-06 / 原6 阻断 — PA S7合法前置及salary定位

- Location: PA2618,2621–2622；C1010,1770–1796；C795–804独立已知separate控制保留。
- Class: test-expectation（非法活动TF/未闭locator）。
- Symptom: no_pa未声明reference/election却有2026-06-01年外TF，未来域校验会先拒；两PA S7缺普通窗口控制；salary拆分未核field/year/p1。
- Prior hypothesis: S7特殊窗口可带未声明年外TF且宽locator足够；F005520/461/551否定。
- Candidate（未授权）: 删除no_pa-only inactive TF，不加无用reference；两PA路径同输入普通窗口控制；salary S7明确field/year/subject_id=p1，不冻结问题原文。
- Acceptance: 当前240k needs_input/sixnull/no264k两独立目标及selectedseparate940/partial/jointunresolved/bestnull不变；PA反例先合法。
- Attempts: 2/2（W0核账：P18→focused反馈→P22聚合，两周期）。Status: BLOCKED（终局；D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；终局FAIL（ora-16）：非法年外TF删除/普通窗口控制已满足；但_question_for的field仅子串包含（C:1063/helper C:526），未核为years.2025_26.household.marriage（处方:54/Approved005:462）。S7缺subject_id P→F为合法新暴露。 **D4-A（2026-10-16，D4=1第三轮）**：C:513–558 field 全等模式；C:1075–1076 完整 field+year+p1；ora-22复审 **RESOLVED/质量闭合**（focused 失败明确为产品侧缺 subject_id，非测试定位宽松）。

## AQA-07 / 原7 高 — CF4无税额／无隐式候选及可见说明

- Location: PA2212,2258–2270,2303–2315；C1323–1333。
- Class: test-expectation（负载保护/错误说明来源）；CF4已批准无新政策。
- Symptom: 仅joint且无独立selected，未核六金额null或排除隐式no_pa/其它scenario；机器码/未获批joint字段可能替代真正可见说明假绿。
- Prior hypothesis: admission/best/scenario与任意说明容器足够；复评否定负载及可见语义闭合。
- Candidate（未授权）: 既有两反证直接保护无确定joint税额/无隐式候选；仅获批可见label/说明同时表达conditional+nonaccepted，不靠机器码，不整句equals。
- Acceptance: 合法ordinary单变异、manual选择日定位、无全局CONTRADICTORY/政府无效/隐式no_pa；保留真正独立selected的合同（本例无）。
- Attempts: 2/2（W0核账：P13→focused反馈→P22聚合/去subject约束，两周期）。Status: BLOCKED（终局；D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；**质量PASS（ora-16，限定）**：六键全null/scenario_ids==[]/requested恰pa_joint（PA:558–565/2332–2344/2390–2402）；双语义仅selection step label（C:1314–1333）。新增负载断言本批未达（两例先败2322/2380），接受的是保护断言质量非行为满足。

## AQA-08 / 原8 阻断/高 — selected对账、nested/Exact/排序及非法前置

- Location: R630–650；PA2956–2964,2981–3031,3089–3090（_fin274）。
- Class: test-expectation（有限覆盖/非法source前置）；Exact负零/前零接受为静态推论非新运行。
- Symptom: no_pa-only不能替代selectedpa_self+paid2000+nextno_pa；run1所有scenario.next3160循环过强，但全撤回不足。nested/shares空覆盖、Exact接受-0/1与00/1；正物业无property分类；投影等价未证规定排序；next_year后段未达。
- Prior hypothesis: 两no_pa反例足以替代R结构；顶层键/空decision/shares与gcd字符串足够闭合；被复评否定。
- Candidate（未授权）: 只补/独立化selectedpa_self六键+next_year3160，非原样恢复过强循环；现有trace用真实非空decision/shares核bases/op/shares/amounts/candidate_checks闭合；零严格0/1；排序输入先合法property来源且核unitID/同类claimID实际顺序。
- Acceptance: 保留2024→25 current2440/next3940与2025→26 940/3160、partial/balancenull，负NAI精确TYPE/fullfield；仅规定trace投影+连续step、不比requested/corehash；不增加新税法/claim矩阵。PA married关联原2，不重复开预算。
- Attempts: 2/2（W0核账：R1新增scenario/next断言→run1失败→R4全删，两周期；评审R5示例项）。Status: BLOCKED（终局；D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；终局FAIL（ora-16）：selected六键/Exact拒-0,1与00,1/property来源等已加；但①旧trace投影内容比较实质缩减（基线:1388–1415→现PA:3411–3432仅ID/顺序，丢金额/bases/operations一致性，无处方依据）；②两next_year节点未核year（R:684–693）；③未保留同税额best→pa_self观察（处方:73）；④assessment顺序仅跨输入相等未证单位ID规定顺序。报告「无弱化」总声明被否。 **D4-A（2026-10-16，D4=1第三轮）**：投影内容比较（kind/label＋去requested完整detail）、两 next_year.year、best→pa_self 三键观察均已恢复；但 assessment 分块排序仍可漏检（PA:3517–3522/3573–3575 仅校验连续片段 unit IDs 升序；静态反例 A:u2→B:v1→A:u1；Approved005:738）——ora-22 **NOT RESOLVED 终局**（D4 授权已用尽）。最小方向：scenario 块唯一+规定候选顺序，或按 scenario 收集原始序列核升序。携入 M0 须另行逐项授权。 **A2=1单点修（2026-10-16）**：方向②落实（PA:3454–3470 块唯一+候选顺序子序+按scenario保序汇集核升序）；反例原形+同族封堵（PA:3679–3697）；控制节点=P；无弱化（旧断言3608–3610保留）；ora-25 **RESOLVED/质量门PASS**（携M0授权需求解除）。

## 验证owner、依赖与Discovered

- 父协调员持有完整四hash/AST/71app与XML元数据；reviewer已独立审质量。import guard是模块观察非constructor spy；安全有限层级PASS，两旧删除拆分语义PASS，M未改。不得宣称runtime outbound spy0或据此启动其它目录。
- 下一步只读咨询有限技术处方/停止条件与原作者可见历史补证，可并行，互不授权写。历史完整性UNKNOWN则保持停止，不凭报告没说第二次默认余量；已证2次终局BLOCKED。
- 无新政策待问；B宿主权限另行具体咨询，P三项2/2终局不变，C未知余量保持停止。不借这些任务跳过acceptor或最终P1 gate。

## 咨询与历史补证归并

- ora-14终结：六项技术上可在原A范围有限闭合，但不保证一次整体修订收敛、不核定任何余量；父级采纳更小处方及停止条件，完整要点见`.slim/deepwork/tax-correctness-stage1/p1-r2-red-a-qa-consultation.md`。无新CF4/状态选择。
- 原作者exe-66只读文字历史已归档`p1-r2-red-a-history-reconstruction.md`。原2/5/6/7披露focused反馈后聚合重写，8披露run1后R全部结构撤回，3称一次成型。作者自编标签/同一计划解释不是计费结论，7pytest不是7尝试；未凭首尾/XML推断完整中间史。
- 当前未核定六项合法剩余次数，也不虚构全部2/2；所有未闭项**UNKNOWN-BLOCKED / WRITE HELD**。各节旧“咨询/历史核账”前置已实际完成，停止原因现在为余量/新执行授权未证，不是还有后台任务。
- 无writer、无新pytest/源码预算消耗；P/C/B共同门禁未闭。停止重复同一缺证咨询/历史回溯，不自动补改测试，不问笼统"继续"。
- **D4-A收官（2026-10-16）**：AQA-02/05/06 质量闭合（ora-22 RESOLVED）；AQA-08 终局未闭（assessment 分块排序残余，详见本板 AQA-08 与 STATE D4-A 节）；六终态 focused=12/3P/9F、8/0P/8F、16/10P/6F、1/0P/1F、4/2P/2F、2/1P/1F；新增实为 17F/13P（报告:69「16」应为14、:74「4P/8F」应为3P/9F，已勘误记录）。
- **AQA-08收官（2026-10-16，A2=1）**：单点点修（exe-72）→ora-25复审 **RESOLVED**；A 板八项全部质量闭合；报告表述勘误 1 条已记录（报告:63 过强表述，不影响闭合）。
