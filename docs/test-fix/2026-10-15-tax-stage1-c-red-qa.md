# Test Fix Board — stage1 C / Red质量有限库存（2026-10-15）

Status: D4-C全闭合（2026-10-16：D4=1第三轮→exe-73交付→ora-23复审PASS：CQA-01/03/04/05 RESOLVED、CQA-02保留；CQA-05四节点P→F裁定ACCEPT（保留wire fullfield期望，不回改内部键）；C未冻结、不放行Green/M0）
Spec: `specs/002-tax-correctness-remediation.md`、Approved005/006；源码根因预算仍见 `2026-10-15-tax-stage1-p1-r2.md`。

## 基线、范围与尝试纪律

- 原C113/81F32P → 第一次有界点修候选366/218F148P/0error0skip。两次质量审阅均NEEDS REVISION；首份 `p1-r2-red-c-review.md`，复评及归并 `p1-r2-red-c-review2.md`。
- 既有四文件批命令：MAIN venv python，cwd COPY，`-m pytest tests/unit/test_002_rules_contract.py tests/engines/test_salaries_engine.py tests/engines/test_002_next_year.py tests/api/test_002_facts_parity.py -q`，唯一Temp DB/basetemp/JUnit及其余参数见作者corrections报告31–33。为建板不重复执行不合格fixture。
- 完整366节点及首失败以Temp/opencode/hktax-r2-red-c-corr-20261015-222545-ec9096ce/junit-final.xml、junit-final-inventory.txt为执行库存；218不是218个已接受产品缺陷。本板只冻结下面5个质量根因，不追修其余产品Red。
- 仅COPY N=`tests/engines/test_002_next_year.py`、U=`tests/unit/test_002_rules_contract.py`、Q=`tests/api/test_002_facts_parity.py`、S=`tests/engines/test_salaries_engine.py`；当前四文件均无新写授权。MAIN只协调记录；不动app/spec/conftest/其它lane/依赖/网络/服务/git。
- 每项最多2次点修。已发生一次整体有界点修；内部具体根因次数即使补原作者可见历史后仍未证完整，**不虚构精确1/2或2/2，也不重新归零**。五项UNKNOWN-BLOCKED（咨询已做但余量未证），不等同已证全部耗尽。任何已核两次项直接终局BLOCKED，不第三试或换owner重置。
- 连续两次质量拒绝后先咨询；每个有合法余量的下一尝试仍须写不同假说/精确范围。现在只库存与归并，不发下一writer。P最终三项BLOCKED与B宿主恢复问题不得由本板绕开。
- 收敛指标是有限质量项闭合/合法控制与无非目标回归，不以修改期待减少产品Red；CF2、TF域、独立fullfield、独立金额冻结不变。源12例外0/1、额外最终P1 gate未用。

## CQA-01 / R1 高 — 安全setup及无条件恢复
- Location: Q:210–277,270–273,347–363,430–464。
- Symptom: 首次patch后至yield前setup不受finally保护；undo/verify失败可跳过restore；connect前未拒绝实际越界路径；安装identity和本地替身counter不在最终检查。
- Class: test-expectation（安全夹具；未观察到真实越界I/O/出站）。
- Prior hypothesis: pre-main真实边界+路径登记+正常teardown足够；复评否定setup/检查异常及每次连接边界闭合。
- Candidate hypothesis（尚未授权）: 从首次patch前外层finally；counter/DB/install独立收集后必恢复；连接前按新store当前函数/cached已核同批Temp拒绝；本地counter纳入收口。
- Suspect files: Q文件内安全层/fixture，不创建shared helper。
- Acceptance: 正常/目标失败/setup失败/close失败及undo/verify失败均恢复原identity；真实路径I/O前核验；ASGI与合法本地availability保留；自检范围准确，不冒称纯文件spy0。
- Attempts: 2/2（W0核账：E19安全层→smoke#1反馈→E34 identity方案修正，两周期；见p1-r2-budget-adjudication.md）。Status: BLOCKED（终局；D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；终局FAIL（ora-17）：restore仍非无条件（Q:519无保护可跳过Q:525）；新store当前函数归属失效（按路径登记Q:278/查询Q:287，改至同批他函数目录得None跳过检查）；安装identity未核（Q:217–221/382–409）；故障未注入（setup自写finally Q:571–579、close未真抛Q:592–597）；「229F同经一个finally」不成立；源码级包裹不满足Acceptance。携入M0须另行授权。详见STATE/p1-r2-wd3c-quality-review.md。 **D4-C（2026-10-16，D4=1第三轮）**：Q:221–225/285/295–313（id+强引用、连接前身份核）/404–407（逐补丁 is installed）/543–554（收口/verify/restore 分护）；五故障驱动真实 `__wrapped__`+实际注入（Q:587–726）；junit-q01 8P；ora-23 **RESOLVED/质量门PASS**。

## CQA-02 / R2 高 — entry和optional首失败遮蔽
- Location: Q:575–585,1191–1275,1645–1671；inventory348等。
- Symptom: pure-longest由HTTP控制先失败；33负例被32正例挡住；aux/manual省略失败使null等后续目标未达。
- Class: test-expectation（验证覆盖）。
- Prior hypothesis: 按entry参数化已隔离首失败；实际控制仍跨入口且三态串行。
- Candidate hypothesis（尚未授权）: 控制同entry；32合法和33非法分离；省略/[]/null独立case，已有短ID一般控制且同步引用。
- Suspect files: Q现有optional/ID/control。
- Acceptance: 纯入口不依赖HTTP成功，33/null目标可独立到达；省略缺席hash与[]合法不互相阻断，CF2和独立literal不变；不复制新C/P矩阵。
- Attempts: 2/2（W0核账：E20–26/E29→q-diag反馈→E35–36 kind-aware修正，两周期；已解决子项不免计）。Status: BLOCKED（终局；D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；**质量PASS（ora-17）**：entry分派Q:708–734、三态24节点独立Q:1337–1468、32/33分离Q:1960–1997、两overlong F→P合法解除遮蔽。

## CQA-03 / R3 中高 — TF有限子项与guard顺序
- Location: Q:1353–1493。
- Symptom: election日期来源缺失，positive entry未分派，HTTP全ok不能证明先于拒绝guard；marriage控制削弱年度事实。
- Class: test-expectation（计划覆盖/合法控制）。
- Prior hypothesis: reference日期矩阵及纯入口48格可支撑完整TF/guard声明；复评否定未执行来源与顺序。
- Candidate hypothesis（尚未授权）: 保留YA事实、独立reference/election来源，positive真正同entry；有限HTTP拒绝guard观察非法TF早退。
- Suspect files: Q现有TF helper/cases。
- Acceptance: 四谓词election来源及相应越界具合法active actual PA；reference不被隐含代用；非法TF准确input错误且guard未达；不改TF域/wire。
- Attempts: UNKNOWN/2（W0核账：可见仅E27一周期；不能证明无后续编辑）。Status: UNKNOWN-BLOCKED（D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；终局FAIL（ora-17）：current顺序观察被首失败遮蔽（Q:1808精确断言先于Q:1813 _NEXT检查；XML:480–494）；「非UNVERIFIABLE」仅证响应码非guard未达；有效子项保留（区间仅追加/election来源/16正例/next guard 4节点）。 **D4-C（2026-10-16）**：Q:1964–1998 顺序（periods==[]）与 Q:2004–2029 精确 (code,field) 拆独立节点、移除 `_NEXT in periods`；完整批 :14187/:14375 真实到达、不再互遮；ora-23 **RESOLVED**。

## CQA-04 / R4 中 — selection依据及PA单变量
- Location: N:1396–1477；作者报告45,57；Approved005:233。
- Symptom: next负例重建assessment时丢actual；selection TYPE理由误以当前API为规范。
- Class: test-expectation（前置与报告归因）。
- Prior hypothesis: 修正通用PA helper即可覆盖next局部重建；局部对象仍未保留合法模式/历史。
- Candidate hypothesis（尚未授权）: 从已证明完整next控制复制，仅改candidate或selection；TYPE依Approved枚举域而非实现。
- Suspect files: N既有candidate/selection cases；另建补充报告保留literal过程历史，不覆盖旧报告。
- Acceptance: 两税×guard原格合法控制可判，唯一目标变异；salary_telepathy仅为不合法CandidateID的TYPE案例，不泛化合法ID未列候选关系；不称四个财务计算缺陷，不改CF。
- Attempts: 2/2（W0核账：E05–10→full#1反馈→E37 EXTRA→TYPE，两周期）。Status: BLOCKED（终局；D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；终局FAIL（ora-17）：_dual_year_input两年共享同一assessment（N:1274–1280/311–312），变异同时改两年→next单变量不成立；PA years.2025_26归属产品Red结论撤回（别名污染）；salary分支blocked候选被吞仍有效（XML:151–193）；TYPE枚举依据正确。 **D4-C（2026-10-16）**：N:1278–1295 两年深拷贝+非别名断言；N:1473–1499/1517–1542 仅 next 单变量；junit-final:1177 available-pa 如实失败（未拒绝/partial/next=null）；ora-23 **RESOLVED**。

## CQA-05 / R5 中 — 投射与不复制claim回归
- Location: N:602–662,1503–1668；completion plan C:46–47。
- Symptom: 教育案例替换掉原不复制claim35890控制；双缺只有income，冲突只有available。
- Class: test-expectation（有限覆盖/回归）。
- Prior hypothesis: 新教育案例与income双缺/冲突足以代表原投射要求；辨识性不同，不可替代。
- Candidate hypothesis（尚未授权）: 保留新教育并恢复原不复制claim；既有双缺补same_mpf，既有冲突available/blocked拆开。
- Suspect files: N现有投射/教育cases。
- Acceptance: 35890不复制claim、same_mpf源/目标缺失、guard不吞假设矛盾独立执行；CONTRADICTORY/fullfield保持，保留3160/12780/0/3160/六键，不重复A排序。
- Attempts: UNKNOWN/2（W0核账：可见仅E11–13一周期；不能证明无后续编辑）。Status: UNKNOWN-BLOCKED（D3已裁定=每项一次新点修，W-D3授权1次）。
- W-D3授权1次（2026-10-16已用）；终局FAIL（ora-17）：两类冲突仅查exc.code无完整exc.field（N:593–597/1654–1657），fullfield未落实；有效增量保留（不复制claim恢复/same_mpf/3160/12780/0/3160）。same_income拆分裁定在范围内。 **D4-C（2026-10-16）**：N:595–603/1670–1677 完整 (code,field)；四节点 P→F 经 ora-23 裁定 **ACCEPT**（保留 wire fullfield 期望，不回改内部键；005:706/178–179 + allowances.py:1141–1151 内部键泄漏暴露）；ora-23 **RESOLVED/质量门PASS**。

## 依赖、验证owner与Discovered

- M-CQA0：复评、预算咨询和原作者只读历史均已归并；完整中间史与合法余量未证，五项无写权。不安排测试重跑。
- 若有合法剩余预算且获准，作者只自己的四文件/唯一Temp安全控制；协调员核完整hash/AST/app集合、所有ID替代/首失败及已有控制；独立审阅质量，之后才可能M0冻结。不得扩目录或跳过acceptor/最终P1 gate。
- 已解决smoke identity误报、PA assumptions模板错误不重开；selection EXTRA→TYPE过程和至少两次完整批如实保留，不凭运行数记补丁数。
- 新发现只登记/分流，不追修；当前没有新税法/CF政策待人重选。宿主变更/额外修复权限仍须人类明确授权，不能由咨询代授。

## 只读历史补证结果

- 完整作者返回及父级采纳见`.slim/deepwork/tax-correctness-stage1/p1-r2-red-c-history-reconstruction.md`；原咨询后续结论见`p1-r2-red-c-qa-consultation.md`。
- 报告称可见E01–E39编辑、R01–R09 pytest（含collection），明确不是按根因尝试计费，亦不能证明中间无其它操作。已披露反馈后修正为R1 identity、R2家族PA assumptions、R4 EXTRA→TYPE；不重开这些已解决点，不拆新预算。
- R3/R5可见记录中无后续方案编辑，不等于已证明只有一次。所有项仍UNKNOWN-BLOCKED，无可执行下一点修。没有新测试运行、COPY写入或生产预算消耗；不凭作者回忆授予余量。
- **D4-C收官（2026-10-16）**：CQA-01/03/04/05 RESOLVED、CQA-02保留 → C 板五项全闭合；CQA-05 P→F 裁定 ACCEPT（合法 wire fullfield 加强断言暴露内部键泄漏）；junit-final=441/237F/204P/0E/0S；指纹三快照一致 fe9dbf7d…；EV=hktax-d4c-20261016-182607。
