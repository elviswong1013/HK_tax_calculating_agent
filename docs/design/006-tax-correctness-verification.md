# 006 — 税负正确性阶段一：验证与历史断言处置附件（Approved 第五稿配套）

Status: Approved
Created: 2026-10-14
Updated: 2026-10-14（第五稿候选→**Approved**：用户 Gate 2 于 2026-10-14 批准「批准第五稿，进入实施」（仅 stage1；未实现）；按 council 四项合同＋`bounded-closure-design.md` 补 availability 调用闭包/replay 失败优先级、reference/report 显式 context、条件 assessment 与 S7 历史配偶、聊天本地覆盖消费者；**review-plan-r5 影响范围正式 APPROVED（design-only；review4 历史保留）**；全表完整 `repo文件::函数`；002 REQ 命名空间）
关联: specs/002-tax-correctness-remediation.md（Approved 第五稿）；docs/design/005-tax-correctness-fact-contract.md（Annex 005，Approved）；docs/prd/002-tax-correctness-remediation.md（Approved）；只读来源：`.slim/deepwork/tax-correctness-stage1/{review-plan-r3,gate-p0-r2,corrective-design,supplementary-design,finite-residual-nodes,ui-singleflight}.md` 及既有 `test-migration*`／`report-adapters`／`test-migration-interface-complete.md`

> 状态声明：本附件是**计划**（Red 尚未创建、pytest/浏览器/服务均未运行）。全部处置条目来自只读源码读取与既有文档记录；**未运行任何测试，不构成行为或金额证明**。旧文件头部的 `REQ-n` 标记只属 001 命名空间 provenance，**不构成 002 行为覆盖证据**；追溯脚本 file-level 命中同样不是行为证明。002 只有 REQ-1..REQ-13：所有“新 REQ”列只写 `REQ-1..13`；未受 002 影响的节点写 `002-N/A（001::REQ-n 原样）`。用户 Gate 2 已于 2026-10-14 批准（「批准第五稿，进入实施」，仅 stage1；未实现）；本附件仍为**计划**，不构成运行或验收证据。

## 1. 范围与效力

补齐 review3、P0-r2、corrective-design、supplementary-design、finite-residual-nodes 与 ui-singleflight 的验证面：历史断言逐节点处置、接口清单、报告适配、B1–B6 具名 Red 计划、八条路径的**实际触发/独立证明**计划、原子消费/失效/观测的确定性竞态计划、浏览器隔离与命令。所有 case/fixture/receipt/seed 产物均为**计划**，Gate 2 后创建。

## 2. 命令与唯一 basetemp（PowerShell；每命令独立唯一目录）

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$guid  = [guid]::NewGuid().ToString('N').Substring(0,8)
$base  = "C:\Users\elvis\AppData\Local\Temp\opencode\pytest-002-$stamp-$guid"

.venv\Scripts\python -m pytest tests/engines tests/api tests/unit -q --basetemp="$base\p1"
.venv\Scripts\python -m pytest tests/ai tests/ui -q --basetemp="$base\p2"
.venv\Scripts\python -m pytest tests/updater -q --basetemp="$base\p3a"
.venv\Scripts\python -m pytest tests/e2e -q --basetemp="$base\p3b"
.venv\Scripts\python -m pytest -q --basetemp="$base\p3c"
```

- 追溯（**拟建新 CLI；当前不存在，不得声称历史支持 002 命名空间**）：`scripts/check_req_traceability.py --spec 002 --require-002 1,2,3,4,5,6,7,8,9,10,11,12,13`。历史脚本只支持 001 的 18-REQ 命名空间；实现后以实际 `--help` 输出为存在证据。
- 竞态测试用**确定性屏障**（事件/BARRIER，不靠 sleep 概率）；413/403 必须断言 ASGI receive 调用次数与 Operation ACK，不只断言 HTTP 码。Chromium 缺失 → **blocked**，skip 不计通过。
- **focused 文件列表为下界、非完整证明**：各阶段完整证据路径以上方目录命令为准；`tests/unit/test_002_report_context.py` 等 report adapter 相关命令在 P3 残差脚本适配时运行，不另设门禁。

## 3. 来源、层级与读取范围

- 逐节点读取：27 文件（§4.3）＋e2e（§4.4）＋`tests/acceptance/test_acceptance_wrong_shape_and_zero_semantics.py`（§4.2）＋`tests/` 全目录 `calc/prepare` 直接调用 grep（§4.1）。
- 层级：T1＝官方原始件；T1-observed＝官方行为观察；T2＝候选锚/研究；T2+手算；T3＝独立手算。PAM39/PAM37 属 T2+手算；B1 的 38,500/47,000/34,250 属**手算 T3 计划**，未执行。旧文件名 REQ 标记不升级层级。

## 4. 完整历史断言处置

### 4.1 纪律、全局规则与 prepare 请求体站点

- Fake v1 isolated bundle 保持 fake（除非 stop-auto/classifier 阻断被专门测试）；旧 v1 历史记录全部保留；replay 7 目标引擎不匹配一致拒绝；历史 web/print/download 保持原 canonical core/input/steps/binding 字节、legacy 字段回退渲染；`steps=[]` 仅 AI `read_result` selected-trace。其余五税数值断言保留；**PA 不适用“其余六目标不变”笼统表述**（PA 迁 v2 partial＋tagged steps）。
- **prepare 请求体站点（下界 ≥10 文件；**不是完整证明**；不得声称已完整，须实现前再 grep）**：
  1. `tests/ai/_ai_fakes.py::run_prepare`（L225–232）
  2. `tests/ui/_helpers.py::run_prepare`（L55–59）
  3. `tests/acceptance/_acc_helpers.py::prepare`（L239–263；`PREPARE_PATH` L255）
  4. `tests/api/test_api_input_three_states.py::_prepare_body`（L50–51）
  5. `tests/api/test_api_period_and_binding.py::_salaries_body`（L28–39）
  6. `tests/api/test_api_headers.py::test_records_api_no_store_headers`（内联 L35–39）
  7. `tests/api/test_api_security.py::test_sensitive_actions_require_csrf_capability`（内联 L130–139；同文件 7 处 `json={}` 探针 L78–127）
  8. `tests/ui/test_ui_confirm_flow.py::test_ui_prepare_needs_input_no_confirmability`（内联 L31–40）
  9. `tests/ui/test_ui_money_strings.py::test_ui_money_as_string_no_parsefloat`（内联 L26–31、L38–43）
  10. `tests/acceptance/test_acceptance_wrong_shape_and_zero_semantics.py::test_acceptance_tax_type_wrong_shape_structured_rejection`（**直接** `api.post(PREPARE_PATH, json=body)` L71；body L65–69）
  其余经 helper 的调用（_acc_helpers/records_steps 等）随 helper 迁移；旧“七个站点”与“九个站点”都不是完整证明。

### 4.2 引擎／PA／records／验收（逐节点全名）

| 完整 node（行） | 现断言/输入 | 处置 | 新 REQ |
| --- | --- | --- | --- |
| `tests/engines/test_salaries_engine.py::test_salaries_pam39_q1_final_940`（L100–113） | 240000/9000；3940/3000/940 | 适配 v2；保留独立算术；T2+手算 | REQ-8/13 |
| `tests/engines/test_salaries_engine.py::test_salaries_pam39_pst_2026_27_35890`（L116–136） | next＋final 35890 | final 保留；next 改 2027/28 缺规则 null/partial | REQ-9 |
| `tests/engines/test_salaries_engine.py::test_salaries_floor_convention_three_years_official`（L139–171） | 三年度取整元组 | 适配；保留 | REQ-8/13 |
| `tests/engines/test_salaries_engine.py::test_salaries_mpf_mandatory_deduction`（L174–210） | joint 段 51420 非法 | 单人保留；**joint 替换为 (b)/(a) 成立合法案例** | REQ-7 |
| `tests/engines/test_salaries_engine.py::test_salaries_unknown_eligibility_questions`（L213–233） | 婚姻/居港 unknown | 婚姻保留；**子女居港断言删除**（REQ-3） | REQ-2/3 |
| `tests/engines/test_personal_assessment.py::test_pa_q32_example_9920_vs_28800`（L191–230） | PA 9920 vs 28800 | 适配；complete→partial | REQ-8/10 |
| `tests/engines/test_personal_assessment.py::test_pa_interest_cap_per_property_nav_share`（L233–268） | 双物业数值 | 保留 | REQ-8 |
| `tests/engines/test_personal_assessment.py::test_pa_standard_cap_pa_reduced_income_not_salary_reuse`（L271–292） | 标准基数不复用 | 保留 | REQ-8 |
| `tests/engines/test_personal_assessment.py::test_pa_compare_after_rebate_not_before`（L295–332） | 缺 paid→no best | 替换为 best 独立于 paid | REQ-10 |
| `tests/engines/test_personal_assessment.py::test_pa_duplicate_claim_blocked`（L334–372） | 重复拒 | 扩展 ledger 矩阵 | REQ-6 |
| `tests/engines/test_personal_assessment.py::test_pa_unknown_qualification_no_optimal_claim`（L375–392） | unknown 六 null | 适配 Tri/TF | REQ-2 |
| `tests/engines/test_personal_assessment.py::test_pa_joint_output_total_authoritative_no_per_person_whole`（L395–429） | joint 19120；零 R | 保留；零收入改合法来源 | REQ-7/8 |
| `tests/engines/test_personal_assessment.py::test_missing_pst_partial_no_total_balance`（L432–450） | 缺 paid | 适配 REQ-9/10 | REQ-9/10 |
| `tests/engines/test_personal_assessment.py::test_pa_pam37_example4_joint_rebate_then_ratio_apportion_internal_fractions`（L452–491） | 36120；4/5、1/5 | 保留 | REQ-8 |
| `tests/acceptance/test_acceptance_dependent_parents.py::test_acceptance_dependent_parents_allowance_computed`（L63–68、149–221） | tier 参数 | 拆两路径/年内年龄；next partial | REQ-4/9 |
| `tests/acceptance/test_acceptance_tax_types_e2e.py::test_acceptance_all_approved_tax_types_end_to_end`（L41–105） | 7 目标 complete | **salary＋PA 均迁 v2 状态**；其余五目标与路由保留；prepare If-Match | REQ-10/11/12 |
| `tests/acceptance/test_acceptance_input_semantics.py::test_acceptance_missing_pst_is_partial_not_zero`（L55–110） | missing vs 0 | 保留边界；next 语义 | REQ-9/10 |
| `tests/acceptance/test_acceptance_input_semantics.py::test_acceptance_unsupported_deduction_positive_rejected`（L112–192） | 正数拒/unknown 追问 | 适配信封；保留 | REQ-2 |
| `tests/acceptance/test_acceptance_wrong_shape_and_zero_semantics.py::test_acceptance_tax_type_wrong_shape_structured_rejection`（L60–94） | 六种非字符串/空 tax_type，直接 POST PREPARE_PATH L71，要求 422 **且** `E_INPUT_TYPE` | **新增站点**；helper/直发都须带 `If-Match`，使该断言在 reservation 后仍可达；**不得弱化为任意拒绝通过** | REQ-2/11 |
| `tests/acceptance/test_acceptance_wrong_shape_and_zero_semantics.py::test_acceptance_zero_amount_unknown_eligibility_still_questions`（L97–184） | baseline 成功取 before_reduction；unsupported `voluntary_mpf_contribution` 零＋unknown→422 `E_ELIGIBILITY_UNKNOWN`＋questions 或 execute 200 needs_input 六 null；零＋false→成功 complete、无 questions、before_reduction＝baseline | **保留三态兼容**（`E_DEDUCTION_UNSUPPORTED` 仅正数；显式零占位接受）；若保留 complete 断言须给完整 current/next fixture（或单列 approved partial delta），不 blanket 弱化 | REQ-2/6/10 |
| `tests/acceptance/test_acceptance_records_steps_round3.py::_drive`（L43–56） | 三段驱动 | 适配 v2＋If-Match | REQ-10/11 |
| `tests/acceptance/test_acceptance_records_steps_round3.py::_check_steps`（L59–81） | steps 非空＋金额＋率/取整 | 薪俸/PA 按 tagged steps 断言（label/decision/assessment 原条目；非权威条件式文案）；其余五税原口径 | REQ-10 |
| `tests/acceptance/test_acceptance_records_steps_round3.py::test_acceptance_records_steps_nonempty_all_engines`（L84–119） | salary partial 后才 `_check_steps`；非薪俸六目标 complete 否则 `continue` | **PA 必须单列：即使 status=partial 仍执行完整 steps 检查，不得以 complete-only continue 跳过**；salary partial 同理；其余五税目标保持 complete 前置 | REQ-10 |
| `tests/acceptance/test_acceptance_bundle_digests_round3.py::_local_snapshot_hashes`（L51–63） | 本地 T1 hash | 保留（不新增官方读取） | REQ-12/13 |
| `tests/acceptance/test_acceptance_bundle_digests_round3.py::test_acceptance_bundle_evidence_digests_complete`（L66–120） | 五项溯源≥5 本地一致 | 保留；v2 束仍须满足 | REQ-12/13（001::REQ-2/9/13 溯源保留） |
| `tests/ui/test_ui_records.py`（9 节点：`_error_code` L49–53、`_get_record` L56–57、`test_ui_stale_response_dropped_history_record_preserved` L63–95、`test_ui_records_survive_editor_and_rules_changes` L101–152、`test_ui_records_memory_vs_export_replay_distinct` L158–207、`test_ui_print_html_inline_no_store_json_download_attachment` L213–251、`test_record_fields_complete` L257–320、`test_replay_requires_pinned_bundle_and_engine_version_exact_equality` L326–367、`test_replay_not_current_reassessment` L370–405） | v1 字段/三入口/重放 | 保留历史/回放不变量；stale 改 invalidate/If-Match revision；导出保留全字段；旧记录 legacy 渲染；**AI-only steps=[] 不套用** | REQ-10/11/12 |
| `tests/ui/test_ui_result_states.py::test_ui_partial_no_balance`（L64–96） | 不合成余额 | 保留 | REQ-10 |
| `tests/api/test_api_input_three_states.py`（`_salaries_input` L34–47、`_prepare_body` L50–51、4 测试 L60–146） | v1 三态/严格/1MB | 迁 v2＋If-Match；保留不变量 | REQ-2/11 |
| `tests/api/test_api_period_and_binding.py`（`_salaries_body` L28–39、2 测试 L41–95） | 年份 pin/越界 | 适配；保留四元组/无回退 | REQ-12 |
| `tests/acceptance/test_acceptance_production_wiring.py::test_acceptance_production_manifests_typed_slots_and_gate_binding`（L91–201） | 8 manifests 非空 | stop-auto；允许真实 slots 空 | REQ-12 |
| `tests/acceptance/test_acceptance_production_wiring.py::test_acceptance_production_change_detection_quarantine_publish`（L204–343） | band/cap/单证 | salary 场景改 stop-auto 观察；保留其余 | REQ-12 |
| `tests/acceptance/test_acceptance_bundle_and_readme.py::test_acceptance_bundle_evidence_and_profiles_populated`（L46–112） | bundle/salary exec | 适配 v2；保留 evidence | REQ-12/13 |
| `tests/acceptance/test_acceptance_bundle_and_readme.py::test_acceptance_readme_complete`（L115–157） | README | 保留（仅版本说明） | 002-N/A（001 原样） |
| `tests/acceptance/_acc_helpers.py::salaries_min_facts`（L118–128）、`::minimal_facts`（L131–237）、`::prepare`（L239–263） | v1 builder | 迁 v2＋If-Match；其他五税 builder 不动 | REQ-11/12 |
| `tests/acceptance/_acc_helpers.py::personal_assessment_min_facts`（L161–197） | PA v1 | 经 PA 断言时迁移 | REQ-8/13 |
| `tests/acceptance/_acc_helpers.py::BUDGET_MANIFEST`（L353–406）、`initial_content`/`changed_bundle_content`（L475–485） | fake salary cap | 保持 fake；stop-auto 期望 | REQ-12 |
| `tests/acceptance/_acc_helpers.py::parent_entry`（L498–515） | v1 parent 包装 | Gate 2 后按获批契约 | REQ-4 |

### 4.3 27 文件接口清单（第二轮全读；逐节点全名）

**API**

| 完整 node（行） | 现契约/断言 | 处置 | 新 REQ |
| --- | --- | --- | --- |
| `tests/api/test_api_headers.py::test_records_api_no_store_headers`（L24–49） | no-store；v1 body L35–39 | 保留；body 迁 v2＋If-Match（站点 6） | REQ-11 |
| `tests/api/test_api_headers.py::test_json_export_and_print_report_content_disposition`（L52–73） | print inline/download attachment | 保留 | REQ-11 |
| `tests/api/test_api_security.py::test_loopback_only_refuses_public_bind`（L41–48） | 回环集合 | 保留 | 002-N/A（001::REQ-14 原样） |
| `tests/api/test_api_security.py::test_host_origin_exact_match_required`（L51–106） | Host/Origin | 保留 | 002-N/A（001::REQ-14 原样） |
| `tests/api/test_api_security.py::test_sensitive_actions_require_csrf_capability`（L109–141） | E_CSRF；body L130–139 | 保留；合法体迁 v2＋If-Match（站点 7）；403 不推进/不读 body | REQ-11 |
| `tests/api/test_api_coverage.py::_coverage_items`（L47–53） | 非空 | 保留 | REQ-1 |
| `tests/api/test_api_coverage.py::test_coverage_page_lists_inventory_with_status`（L56–74） | 八字段 | 迁 compute/needs/manual＋phase＋limits_zh | REQ-1 |
| `tests/api/test_api_coverage.py::test_personal_assessment_labelled_election`（L77–91） | PA 评税选择 | 保留 | REQ-1 |
| `tests/api/test_api_coverage.py::test_unimplemented_no_calc_entry`（L94–120） | 无入口 | 保留不变量 | REQ-1 |

**AI**

| 完整 node（行） | 现契约/断言 | 处置 | 新 REQ |
| --- | --- | --- | --- |
| `tests/ai/_ai_fakes.py::FakeModelOutbound`（L110–137） | 出站接缝 | 保留 | REQ-11 |
| `tests/ai/_ai_fakes.py::wire_text_body`（L60–68）、`::wire_tool_calls_body`（L71–104） | wire 构造 | 保留 | REQ-11 |
| `tests/ai/_ai_fakes.py::salaries_input`（L210–222）、`::run_prepare`（L225–232） | v1 平面 | 迁 v2＋If-Match（站点 1） | REQ-11/12 |
| `tests/ai/_ai_fakes.py::run_confirm`（L235–246）、`::complete_calc`（L249–263） | 三段 | 适配 revision/快照 | REQ-11 |
| `tests/ai/_ai_fakes.py::publish_alt_bundle`（L266–274） | fake 发布 | 保留 fake | REQ-12 |
| `tests/ai/conftest.py::AiApi`（L41–74）、`::AiHarness`（L77–100）、`::_build_app`（L103–121）、`::ai`（L124–129）、`::ai_offline`（L132–137） | 临时 DB＋假出站 | 保留 | REQ-11 |
| `tests/ai/test_ai_outbound.py::test_local_calc_without_model`（L91–144） | 本地可用/409 不外发 | 适配 fixture；保留 | REQ-11 |
| `tests/ai/test_ai_outbound.py::test_consent_freezes_full_bytes_incl_history_system_tools`（L147–184） | 五键＋tools＋SHA | 保留；不外扩 | REQ-11 |
| `tests/ai/test_ai_outbound.py::test_consent_one_use_consumed_before_send`（L187–212） | 发送前消费 | 保留 | REQ-11 |
| `tests/ai/test_ai_outbound.py::test_context_change_new_grant`（L215–265） | 变化 stale | 保留 | REQ-11 |
| `tests/ai/test_ai_outbound.py::test_keys_server_only_never_browser_log_export`（L268–311） | 密钥服务端 | 保留 | REQ-11 |
| `tests/ai/test_ai_outbound.py::test_model_failure_no_leak_form_preserved`（L314–362） | 502/脱敏 | 保留 | REQ-11 |
| `tests/ai/test_ai_outbound.py::test_clear_session_memory_only_external_prints_kept`（L365–416） | 清空语义 | 保留（clear epoch 语义见 §5） | REQ-11 |
| `tests/ai/test_ai_outbound.py::test_model_minimal_payload_no_full_export_or_unused_facts`（L419–467） | 最小化 | 保留；marker 更新 v2 | REQ-11 |
| `tests/ai/test_ai_tool_gating.py::test_structured_confirm_before_calc`（L76–134） | 伪造确认拒 | 保留；propose/validate 完整信封 | REQ-11 |
| `tests/ai/test_ai_tool_gating.py::test_explanation_amounts_from_tool_result_only`（L137–172） | read_result 金额 | 改最小保存结果 | REQ-11 |
| `tests/ai/test_ai_tool_gating.py::test_input_mutation_invalidates_results`（L175–207） | 修订 stale | 改 invalidate/If-Match | REQ-11 |
| `tests/ai/test_ai_tool_gating.py::test_unsourced_rate_refused`（L210–240） | unconfirmed | 保留 | REQ-11 |
| `tests/ai/test_ai_tool_gating.py::test_new_tool_recommendation_needs_confirmation`（L243–265） | ignored＋确认 | 保留 | REQ-11 |
| `tests/ai/test_ai_tool_gating.py::test_confirmation_wrong_target_not_consumed`（L268–297） | 错目标不消费 | 保留 | REQ-11 |
| `tests/ai/test_ai_tool_gating.py::test_unknown_tool_no_action_strict_params_rejected`（L300–371） | strict/批/JSON/id | 保留；read_result 对齐 | REQ-11 |
| `tests/ai/test_ai_startup.py::test_startup_single_worker_no_proxy_headers_loopback`（L31–78） | run() | 保留 | 002-N/A（001::REQ-14 原样） |
| `tests/ai/test_ai_transport.py::test_stream_read_1mb_accumulation_cap`（L40–75） | 1MB | 保留 | 002-N/A（001::REQ-14 原样） |
| `tests/ai/test_ai_transport.py::test_httpx_sends_frozen_bytes_no_reserialization`（L78–143） | 冻结字节 | 保留 | 002-N/A（001::REQ-14 原样） |

**UI**

| 完整 node（行） | 现契约/断言 | 处置 | 新 REQ |
| --- | --- | --- | --- |
| `tests/ui/_helpers.py::salaries_input`（L35–48）、`::prepare_body`（L51–52）、`::run_prepare`（L55–59） | v1 | 迁 v2＋If-Match（站点 2） | REQ-11/12 |
| `tests/ui/_helpers.py::run_confirm`（L62–74）、`::run_execute`（L77–79）、`::complete_calc`（L82–101） | 三段 | 适配 revision | REQ-11 |
| `tests/ui/_helpers.py::inject_record`（L104–109）、`::publish_alt_bundle`（L112–124）、`::binding_four_keys`（L127–134） | 接缝/四元组 | 保留 | REQ-10/12 |
| `tests/ui/_helpers.py::WorkbenchAudit`（L141–209）、`::audit_workbench`（L205–209）、`::strip_media_blocks`（L212–232）、`::fixed_width_violations`（L235–242）、`::fetch_asset`（L245–249） | SSR 审计 | 保留 | REQ-12 |
| `tests/ui/conftest.py::UiApi`（L27–60）、`::UiHarness`（L63–86）、`::ui`（L89–100） | 临时 DB | 保留 | REQ-11 |
| `tests/ui/test_ui_chat.py::test_ui_chat_panel_structure_a11y`（L304–351） | 结构/aria/375 | 保留 | REQ-11 |
| `tests/ui/test_ui_chat.py::test_ui_chat_unconfigured_state`（L354–381） | 未配置 | 保留 | REQ-11 |
| `tests/ui/test_ui_chat.py::test_ui_chat_send_opens_real_preview`（L384–432） | 真实预览 | 保留 | REQ-11 |
| `tests/ui/test_ui_chat.py::test_ui_chat_deny_keeps_local_only`（L435–461） | 拒绝不发 | 保留 | REQ-11 |
| `tests/ui/test_ui_chat.py::test_ui_chat_allow_grants_and_sends_once`（L464–511） | 单次发送 | 保留 | REQ-11 |
| `tests/ui/test_ui_chat.py::test_ui_chat_new_message_requires_new_preview`（L514–558） | 新授权 | 保留 | REQ-11 |
| `tests/ui/test_ui_chat.py::test_ui_chat_tool_result_rendered`（L561–618） | proposed_input 回带 | 改 checked-proposal 契约；propose 完整信封 | REQ-11 |
| `tests/ui/test_ui_chat.py::test_ui_chat_model_error_preserved`（L621–663） | 超时保留 | 保留 | REQ-11 |
| `tests/ui/test_ui_confirm_flow.py::test_ui_prepare_needs_input_no_confirmability`（L27–89） | unknown 无能力 | 迁 v2＋If-Match（站点 8）；保留 | REQ-11 |
| `tests/ui/test_ui_confirm_flow.py::test_ui_explicit_user_confirm_before_execute`（L92–137） | prepared≠exec | 保留 | REQ-11 |
| `tests/ui/test_ui_consent.py::test_ui_consent_new_payload_new_grant`（L37–97） | 负载变化 | 保留 | REQ-11 |
| `tests/ui/test_ui_money_strings.py::test_ui_money_as_string_no_parsefloat`（L20–76） | number→422 | 字段路径迁 v2；站点 9 | REQ-2/12 |
| `tests/ui/test_ui_pages_structure.py::test_ui_focus_aria_responsive_accessibility`（L33–97） | a11y/375 | 保留；M4 必跑 | REQ-11 |
| `tests/ui/test_ui_session_privacy.py::test_ui_revoke_keeps_local_records_discards_pending_ai`（L30–89） | 撤回只废 AI | 保留 | REQ-11 |
| `tests/ui/test_ui_session_privacy.py::test_ui_clear_all_tabs_no_financial_persistence`（L92–148） | 双 epoch | 保留；clear epoch 语义按 §12.3 | REQ-11 |
| `tests/ui/test_ui_update.py::test_ui_update_check_202_shared_job`（L53–94） | 202 幂等 | 保留（P3） | 002-N/A（001::REQ-17/18 原样） |

**Updater**

| 完整 node（行） | 现契约/断言 | 处置 | 新 REQ |
| --- | --- | --- | --- |
| `tests/updater/_fakes.py::FakeClock`（L64–77）、`::FakeOutbound`（L80–104）、`::StrictOutbound`（L107–112）、`::FakeHarness`（L115–131）、`::FakeEngine`（L134–148） | 假件 | 保留 | REQ-12 |
| `tests/updater/_fakes.py::BUDGET_MANIFEST`（L154–204）、`::SCH43_MANIFEST`（L206–243）、`::SDO_INDEX_MANIFEST`（L245–264） | fake manifest | 保持 fake；salary 旧路径转 observer guard；可发布候选走不受影响路径 fake | REQ-12 |
| `tests/updater/_fakes.py::base_sources`（L338–343）、`::sources_with_law`（L346–350）、`::base_documents`（L353–358） | 两/三源 | 保留 | REQ-12 |
| `tests/updater/_fakes.py::changed_bundle_content`（L419–423） | 旧 salary cap | 按新矩阵改 | REQ-12 |
| `tests/updater/test_archive.py::test_snapshot_pin_first_version_never_pruned`（L65–122） | 快照 pin | 保留 | 002-N/A（001::REQ-18 原样） |
| `tests/updater/test_classifier.py::test_data_only_candidate_frozen_schema`（L51–88） | 候选/冻结面 | 增 old/new 共享路径隔离 guard | REQ-12 |
| `tests/updater/test_classifier.py::test_semantic_change_to_change_request`（L91–110） | 语义变化 CR | 保留 | REQ-12 |
| `tests/updater/test_parser.py::test_auto_slot_manifest_fields_complete_and_unverified_locator_not_confirmed`（L58–111） | manifest 门 | 保留 | 002-N/A（001::REQ-18 原样） |
| `tests/updater/test_publisher.py::test_update_publish_atomic_and_audit`（L101–160） | 原子/audit/门 | 适配 purpose/CAS/required 重算 | REQ-12 |
| `tests/updater/test_publisher.py::test_rollback_audited_pointer_not_time_travel`（L163–211） | 回滚 | 保留 | REQ-12 |
| `tests/updater/test_scheduler.py::{test_first_startup_due L121–139、test_month_end_clamp_31jan_feb28_next_anchor_mar L142–189、test_overdue_single_coalesced_catchup L192–219、test_manual_same_pipeline_singleflight L222–280、test_attempt_vs_success_per_source_global L283–314、test_checked_ok_distinct_from_published L317–340、test_manual_reanchor_on_actual_completion L343–373、test_no_financial_data_no_llm L376–402、test_backoff_bounded_in_process L405–450、test_source_states_enter_clear_and_due_is_not_expired L453–492、test_offline_verified_snapshot_with_notice L495–526}` | 调度既有契约 | 全部保留（002-N/A，001::REQ-17/18 原样） | 002-N/A（001 原样） |
| `tests/updater/test_scheduler.py::test_known_change_or_expired_refuses_affected`（L528–581） | 未核/过期阻断 | 保留；增 observer pending 同步不得清除（§5/§7.2） | REQ-12 |
| `tests/updater/test_testgate.py::test_bad_candidate_parameters_rejected_even_if_engines_agree`（L87–133） | 双引擎一致非证据 | 保留；绑定 candidate hash/profile/evidence | REQ-12 |
| `tests/updater/test_testgate.py::test_candidate_gate_isolated_from_current_store_and_network`（L136–186） | 隔离 | 改**实际观察**（§7.2），禁 hardcoded 0 | REQ-12 |
| `tests/updater/test_testgate.py::test_missing_required_case_quarantines`（L189–239） | required 缺失 | 保留；required 由 purpose/diff 重算 | REQ-12 |
| `tests/updater/test_updater_safety.py::test_updater_never_edits_source_tests_specs`（L69–107） | 树摘要 | 保留 | 002-N/A（001::REQ-18 原样） |
| `tests/updater/test_validator.py::test_independent_source_fact_validation`（L43–74） | 独立 T1 | 保留 | 002-N/A（001::REQ-18 原样） |

### 4.4 e2e 文件（隔离与 v2 接线受影响）

| 完整 node（行） | 现契约/断言 | 处置 | 新 REQ |
| --- | --- | --- | --- |
| `tests/e2e/test_ui_browser.py::_free_port`（L27–32）、`::base_url`（L35–66）、`::page`（L69–84） | 真实 uvicorn＋临时 DB | **§6 顺序**：先唯一 DB env＋假网络（导入 main 前）→只导入 main→替换 `_start_scheduler_threads`→显式构造/启动被注入 app；Chromium blocked 可报告 | REQ-11/12 |
| `tests/e2e/test_ui_browser.py::test_core_flows_375_and_1280`（L87–107） | 首页流程 | 保留；M4 经 v2 链路 | REQ-11 |
| `tests/e2e/test_ui_browser.py::test_ui_dialog_focus_and_escape_e2e`（L110–138） | 弹窗焦点 | 保留 | REQ-11 |
| `tests/e2e/test_ui_browser.py::test_ui_aria_live_and_keyboard_e2e`（L141–159） | aria/键盘 | 保留 | REQ-11 |
| `tests/e2e/test_ui_forms_browser.py::base_url`（L72–106）、`::page`（L109–129） | 真实 uvicorn | 同 §6 顺序 | REQ-11/12 |
| `tests/e2e/test_ui_forms_browser.py::_select_by_option_text`（L133–141）、`::_fill_profits_facts`（L144–156）、`::_fill_property_q7_facts`（L159–162）、`::_fill_lease_facts`（L165–171） | 三税填充 | 保留 | 002-N/A（001::REQ-4/5/7 数值原样） |
| `tests/e2e/test_ui_forms_browser.py::_confirm_and_calculate`（L175–188）、`::_expect_complete_result`（L191–211）、`::_assert_no_horizontal_scroll`（L214–221） | 确认/徽标/横滚 | 保留 | REQ-11 |
| `tests/e2e/test_ui_forms_browser.py::test_ui_profits_form_end_to_end_e2e`（L225–233）、`::test_ui_property_form_end_to_end_e2e`（L236–248）、`::test_ui_stamp_lease_form_end_to_end_e2e`（L251–259）、`::test_ui_form_mobile_375_e2e`（L262–272） | 150000/14400/150 | 保留 | 002-N/A（001::REQ-4/5/7 原样） |
| `tests/e2e/test_ui_chat_browser.py::base_url`（L50–92）、`::page`（L95–114） | 未配置态 | 同 §6 顺序 | REQ-11/12 |
| `tests/e2e/test_ui_chat_browser.py::test_ui_chat_unconfigured_preview_escape_e2e`（L117–169） | 预览/Escape | 保留 | REQ-11 |

### 4.5 报告与版本适配（保留报告编号与 provenance）

| 函数/行 | 现契约 | 必需适配 | 新 REQ |
| --- | --- | --- | --- |
| `scripts/official_calc_compare/local_engine.py::run_salaries`（L45–62；caller L127–131） | v1 平面 salary | 改 v2 事实＋显式 availability；next 不回退 | REQ-9/12 |
| `scripts/official_calc_compare/run_all.py::render_report`（L173–522；salary L197–248、摘要 L383–432、next L434–458） | “002”报告/回退文案 | 缺规则→unavailable/null；保留其他对比与编号 | REQ-9/13 |
| `scripts/official_calc_compare/run_all.py::main`（L525–646；L552–563、623–643） | live 驱动 | 经 raw-case 适配器；保留 provenance | REQ-12/13 |
| `scripts/generate_validation_report.py::_single`（L89–97）、`::_person`（L138–157）、`::_pa_facts`（L159–167） | legacy builders | 保留供非目标引擎；新增 v2 builder | REQ-12 |
| `scripts/generate_validation_report.py::_a_pam39_q1`（L313–334）、`::_a_pam39_q2`（L336–360） | Q1/Q2 | Q2 next null/partial；保留标签 | REQ-9/13 |
| `scripts/generate_validation_report.py::_a_pa_q32`（L392–424）、`::_a_pam37_ex4`（L426–464） | PA 数值/比例 | Gate 2 后适配；不把 T2 升 T1 | REQ-8/13 |
| `scripts/generate_validation_report.py::_c_missing_provisional`（L982–1011） | 缺 paid | 保留；002 命名空间映射 | REQ-9/10 |
| `scripts/generate_validation_report.py::_c_unsupported_deduction`（L1014–1048）、`::_c_unknown_eligibility`（L1051–1071） | 正数拒/unknown 追问 | 适配 salary 输入；property 不动 | REQ-2 |
| `scripts/generate_validation_report.py::collect_batch_a`（L847–873）、`::collect_batch_b`（L887–927）、`::collect_batch_c`（L1112–1121） | A/B/C 批 | 过滤 salary/PA；模拟批非证据 | REQ-12 |
| `scripts/generate_validation_report.py::_validate_test_names`（L1124–1148）、`::build_report`（L1179–1304）、`::main`（L1307+） | 001 报告命名 | 分离 002 命名空间；保留旧编号 | REQ-12/13 |
| `scripts/check_req_traceability.py::collect_report`（L30–57）、`::main`（L60–103） | 18 REQ/整档扫描 | 增 002 命名空间；整档标记不算行为覆盖 | REQ-13 |
| `pyproject.toml`（L5–9） | 0.1.0 | 发布时 0.2.0；依赖不动 | REQ-12 |
| TLS/CI/依赖 | 既有 | 不动；不扩 live 对比 | 002-N/A（原样） |

**第五稿 A 合同补充（reference/report 显式 context；PLANNED）**：
- `app/updater/reference.py:33–38,57–95,98–109,121–137,199–241`：原 facts、两个 runner、static/candidate 分派；`ReferenceHarness.run_case`（L199–215）仍只给独立固定阶段/期望，不执行生产引擎；`CandidateEngine`（L218–242）domain 回填只允许替换成完整 static baseline 路径，affected candidate 不允许任何回填。
- `scripts/official_calc_compare/local_engine.py::run_salaries:45–62` 及 `run_all_cases:127–131`；`scripts/generate_validation_report.py` 直接调用点 `:314,337,398,442,902,989,1033,1054`（对应 Q1/Q2/Q32/PAM37、batch B 与三个 C 类 case）；`_single/_person/_pa_facts` 仅作 legacy builders、不得成为默认补资格器；两税改用逐 case 适配。
- 报告本地上下文须明确标“**固定束核验/模拟，不代表实时规则可用性**”；`run_all.py` 保持既有比较范围/TLS/runner 编号，不新增 live PA；`finite-residual-nodes.md` 的 422/code、零额 unknown 与 PA partial steps 断言保持，不得用缺 context 的提前拒绝替代原定断言。
- **Provenance（F §2.3）**：reference/report 逐 case 使用内部 `AdaptedCase` sidecar（`field_provenance: source|synthetic_control`＋`raw_case_hash/controls_hash/context_policy`，非 wire）；新语义字段无实际来源或显式获批 control → case 不可计算、必需 case 隔离。具名 Red 须给出**阶段/断言**：`test_002_reference_missing_evidence_not_auto_qualified` 断言“缺失即隔离、不产 available、不删 case/不改 expected”；`test_002_case_context_cannot_admit_public_request` 断言“case 身份不进入公开 HTTP/AI 准入”；`test_002_reference_static_candidate_explicit_context_and_v2_facts` 断言 static 完整 baseline 与原样 candidate 分流及逐 case 事实来源。

## 5. B1–B6／补充／UI7 具名 Red 计划（全部 **PLANNED**；映射 002 REQ）

### 5.1 B1 两遍/归属/提名（REQ-7；T3 手算）

- `tests/engines/test_002_salaries_couples.py::test_002_joint_spouse_dda_conditional_admission_no_cycle`（T3：500k/200k、MPF 18k、p1 申索 p2 spouse DDA、nominee=p1 → separate 38,500／joint 47,000／conditional 34,250；(b) 成立；**未执行**）。
- `tests/engines/test_002_salaries_couples.py::test_002_reverse_salary_nominee_no_wrong_grant`（反向 nominee 不得沿用 34,250）。
- `tests/engines/test_002_salaries_couples.py::test_002_child_dda_follows_child_nominee_without_base_claim`；`::test_002_spouse_dda_uses_s29_2_responsible_not_child_nominee`；`::test_002_s10a_ambiguous_responsible_only_joint_unresolved`（不清空独立 separate 结果）；`::test_002_conditional_final_only_candidate_checks_not_scenario`。
- `tests/api/test_002_salaries_couples_api.py::test_002_actual_hypothetical_nomination_wire_fields`（同名/冲突/none/unknown/disputed 路径校验；REQ-7/11）。

### 5.2 B2 薪俸窗口（REQ-7）

- `tests/engines/test_002_salaries_couples.py::test_002_salary_separation_window_not_global_ineligible`（替换旧 `test_002_living_apart_event_blocks_joint`）。
- `::test_002_clean_marriage_transition_s10_5`（一次结婚、全年合并不按月份缩减）；`::test_002_partial_child_facts_do_not_trigger_household_manual`；`::test_002_declared_spouse_unknown_marriage_needs_input_not_contradictory`；`::test_002_single_parent_anytime_exclusion_unchanged`。

### 5.3 B3/单位覆盖（REQ-8）

- `tests/engines/test_002_pa_allowances.py::test_002_candidate_effective_salary_pa_matrix`；`::test_002_actual_no_pa_joint_salary_not_silently_split`；`::test_002_pa_self_effective_election_changes_spouse_s29`；`::test_002_mixed_shared_unit_not_double_counted`（unsupported＋unresolved 竞争者）；`::test_002_all_pa_all_no_pa_ordinary_partnership_preserved`；`::test_002_actual_pa_entries_per_person_not_collapsed`；`::test_002_effective_pa_entry_closed_map_no_scalar_collapse`（`pa_by_person` 封闭映射；paJoint `absorbed_by_pa` 不虚构 joint election）。
- **CF4（P5 窄澄清；PLANNED；未执行）**：`tests/engines/test_002_pa_allowances.py::test_002_pa_joint_actual_uses_election_date_not_ya_ordinary_marriage`（既有计划节点；本条目为其参数化覆盖）参数化「选择日已知未婚」「选择日已知分居」两类 P2 已知反证，control＝合法 actual joint ordinary、仅目标选择日事实变化。期望：**相依 joint 候选 unresolved/manual**（field/year 定位选择日婚姻/分居事实；不宣称政府选择无效、不回退默认）、`best=null`、**无确定 joint 税额**（无 `pa_joint` scenario）；**真正独立、已知且合法**的 selected 存在时保留（照 §9.3），**不为保留它强造违反 actual 模式合同的候选**。既有配对 mode/日期矛盾节点仍期望 `E_INPUT_CONTRADICTORY`（不变）；不新增强制 `no_pa`/hypothetical 候选或 P1 要求。

### 5.4 R3#4 兼容（REQ-2/6/9）

- `tests/engines/test_002_claims_tristate.py::test_002_home_status_tf_prior_year_care_period`；`::test_002_item_type_unsupported_positive_rejected_zero_accepted`（`E_DEDUCTION_UNSUPPORTED` 仅正数；显式零 unknown 仍 questions；零 false 无 questions/无影响）；`::test_002_erce_unknown_missing_still_needs`；`::test_002_eligibility_true_cannot_bypass_raw_facts`；`::test_002_hk_presence_days_union_of_explicit_years`。
- **有符号余额（REQ-10）**：`tests/engines/test_002_result_semantics.py::test_002_negative_salary_balance_full_context_refund`（当前组件全已知＋paid＞final → 负 balance＝估算退税；PA balance 仍 null；不得放开 NAI/care/caps 等输入为负；**未执行**）。
- **CF1–CF3（定位与可选性澄清；PLANNED；未执行）**：以 `tests/api/test_002_facts_parity.py` 既有节点承载——`::test_002_closed_layer_extra_matrix_both_tax_types` 与 `::test_002_closed_layer_extra_matrix_subobjects`：EXTRA 矩阵按 **tax×HTTP/pure** 独立参数化；`E_INPUT_EXTRA` 期望 code/路径**取自已批准字面**（005 §13／§4.7），**不把被测 HTTP 行为当 oracle**；`error.field` 为完整 input 内容根路径且含违规键；仅路径不同不构成重复的缺陷主张。
- `::test_002_year_required_key_missing_vs_null`：Year 必填矩阵**排除 `manual_issues`**（改为可选）；其余**八个必填容器**（participants/subject_facts/household/relations/financials/care_payments/claims/assessment）缺席 → `E_INPUT_MISSING`、显式 `null` → `E_INPUT_TYPE`（`null` 侧精确 `years.<YA>.<key>`，HTTP/pure 同一路径）。
- `::test_002_presence_auxiliary_years_follow_explicit_years`：仅当前显式 Year 的 Y+2（不在其 `{Y−1,Y,Y+1}` 并集内）→ `E_INPUT_EXTRA`；**显式 next Year 使 aux Y+2 允许**；普通 TF 域不因此扩张；`error.field`＝真实输入路径 `hk_presence_days[i].year`。
- 新增计划节点 `::test_002_manual_issues_optional_presence_preserved`（**PLANNED；未实现、未执行**）：省略（保持缺席；**不得把缺席静默补成 `[]` 以取 hash**）／显式 `[]` 合法／显式 `null` → `E_INPUT_TYPE`；按「本年＋显式 next 年」及 HTTP／两支纯引擎（适用处）覆盖。
- 既有 `::test_002_optional_auxiliary_arrays_null_is_absent_not_eligibility` **须被替换/纠正**为「省略（保持缺席）／显式 `[]` 合法／显式 `null` → `E_INPUT_TYPE`」的独立正面覆盖，**不得沿用其 wrong Green**（把 `null` 当“未提供”）。
- 边界：以上均为 **PLANNED／未执行**，非 Green/验收主张；CF1 的精确 `error.field` 路径格式**不外溢**到 A4 内部 metadata `field`（保持既有 nullable、形状未冻结）。

### 5.5 协议/消费/清空（REQ-11；确定性屏障）

- `tests/engines/test_002_consume_semantics.py::{test_002_consume_snapshot_survives_edit_before_compute, test_002_execute_never_refetches_prepared_after_consume, test_002_consume_clear_complete_discards_old_record_and_link, test_002_completion_atomic_idempotent_and_conflict_safe, test_002_wrong_target_and_guard_failure_do_not_consume, test_002_consumed_reuse_returns_only_saved_record_link}`。
- CR-P：`tests/engines/test_002_consume_semantics.py::{test_002_completion_uses_confirmation_not_prepared_deadline, test_002_consume_does_not_extend_confirmation_deadline, test_002_complete_at_and_after_confirmation_deadline, test_002_stored_completion_idempotency_survives_confirmation_expiry}`。使用可控 monotonic 时钟、不 sleep：prepare=0、confirm=100、ttl=300 时，complete=350 不得因 prepared deadline=300 而丢弃；confirmation deadline=400，恰在 400 完成有效，`>400` 才过期；consume 不刷新到期点；TTL 后同 hash 仍 already_stored、异 hash 仍冲突，已存结果不撤销。
- `tests/api/test_002_confirmations.py::{test_002_prepare_matching_oversize_advances_and_acks_413, test_002_prepare_stale_or_missing_if_match_oversize_409_no_advance, test_002_prepare_security_403_no_body_read_no_revision, test_002_prepare_body_read_after_auth_and_reservation_only, test_002_prepare_stream_oversize_bounded_and_invalidated, test_002_operation_ack_not_replaced_by_other_editor_revision, test_002_edit_during_prepare_uses_acked_revision, test_002_clear_session_epoch_resets_revision_and_drops_late_replies}`（ASGI receive 计数与 Operation ACK 断言）。
- `tests/e2e/test_002_household_browser.py`＋UI 七场景（§6.7，REQ-11）。

### 5.6 Observer（REQ-12）

- `tests/updater/test_002_observer.py::{test_002_observer_pending_survives_notice_sync, test_002_observer_pending_same_value_does_not_clear, test_002_observer_pending_survives_failure_restart_rollback, test_002_observer_clear_requires_matching_resolution, test_002_observer_commit_has_no_unblocked_read_gap, test_002_observer_anchor_parse_scalar_failures_persist_guard, test_002_observer_failure_before_final_commit_leaves_checking_block, test_002_observer_other_source_failure_cannot_skip_guard_commit, test_002_observer_trailing_zero_format_is_noop, test_002_observer_unapproved_currency_cleanup_rejected, test_002_resolution_must_match_latest_failed_event, test_002_resolution_baseline_change_cannot_reuse}`。

### 5.7 证明/seed/门禁负例（REQ-12/13；清单见 §7.2）

### 5.8 第五稿具名 Red（PLANNED；Advisory §5 全量；A–D 合同）

- Replay/A：`tests/ui/test_002_records.py::{test_002_replay_explicit_context_preserves_pinned_bundle, test_002_replay_missing_or_denied_context_never_defaults_available}`；**扩充既有计划节点 `tests/ui/test_002_records.py::test_002_all_7_replays_refused_engine_mismatch`（补“版本不等先拒绝、未调用 context provider”断言，不造重名副本）**；保留既有同引擎/原 bundle 成功节点（`tests/ui/test_ui_records.py::test_ui_records_survive_editor_and_rules_changes` L101–153、`::test_ui_records_memory_vs_export_replay_distinct` L158–207、`::test_replay_requires_pinned_bundle_and_engine_version_exact_equality` L326–367、`::test_replay_not_current_reassessment` L370–405）。
- Reference/report/A：`tests/updater/test_002_reference_context.py::{test_002_reference_static_candidate_explicit_context_and_v2_facts, test_002_reference_missing_evidence_not_auto_qualified, test_002_case_context_cannot_admit_public_request}`；`tests/unit/test_002_report_context.py::test_002_salary_pa_direct_report_calls_require_bound_context`（参数化上列全部直接调用点）。
- Assessment/B：`tests/api/test_002_assessment_dependencies.py::{test_002_single_salary_api_ui_without_inactive_assessment_fields, test_002_assessment_fields_follow_active_path_without_fabricated_defaults}`（活动 unknown、actual PA 日期冲突、独立已知 separate 保留）。
- CR-E：`tests/engines/test_002_salaries_couples.py::{test_002_election_issue_id_requires_year_participant, test_002_election_issue_only_blocks_dependent_candidates}`（election issue id 必须是同一 Year participant pID，非 participant 及跨年 pID 不满足；仅依赖该选择的候选 unresolved/manual，可能竞争者使 `best=null`，独立已知显式 `selected` 保留；不得把 manual 候选当作 ineligible 删除）。
- S7/C：`tests/engines/test_002_salaries_couples.py::{test_002_sequential_spouse_change_reaches_s7_manual, test_002_conflicting_spouse_identity_remains_contradictory}`（同 YA 完整 fixture＋结构负例）。
- Chat/D：`tests/ai/test_002_chat_coverage.py::{test_002_chat_coverage_consumes_shared_source_and_preserves_unknown, test_002_chat_never_claims_stage2_stage3_or_pa_web_support}`。
- e2e/D＋B：`tests/e2e/test_002_household_browser.py::test_002_single_salary_no_inactive_assessment_controls`；`tests/e2e/test_002_chat_browser.py::test_002_chat_local_coverage_offline_no_outbound`（未配置/离线本地查看；preview/grant/chat 调用均 0；后续发送仍为独立 consent）。
- **Designer 五新 flow 25–29（U004 计划；REQ/定位断言）**：25 `tests/e2e/test_002_household_browser.py::test_002_single_salary_no_inactive_assessment_controls`（REQ-7/11；页面/wire 不出现或补 actual/PA/date/history；final 940、partial、next/balance null；actual/hypothetical 两 basis）；26 `tests/ui/test_002_household_form.py::test_ui_conditional_assessment_unknown_keeps_known_separate`（REQ-2/7/8；活动 unknown 精确追问，inactive 不出现，已知 separate 保留且 best=null）；27 `tests/e2e/test_002_household_browser.py::test_002_same_ya_sequential_spouse_history_manual`（REQ-2/7；locator `years.2025_26.household.marriage`/year=2025_26/subject_id=p1；不产家庭总税；结构负例不变）；28 `tests/ai/test_002_chat_coverage.py` 两节点（REQ-1/11）；29 `tests/e2e/test_002_chat_browser.py::test_002_chat_local_coverage_offline_no_outbound`（REQ-1/11；locator `chat-local-coverage-toggle`/`chat-local-coverage-panel`；preview/grant/chat/outbound 均 0；chatHistory/messages 不增；后续显式发送仍走冻结 preview/单次 grant/一次 send）。**旧 24 flow/7 queue 场景保留。**

## 6. 浏览器／端到端隔离（M4＋P3）

1. **隔离顺序（不可跳过或调换）**：① 先设置唯一临时 `HKTAX_DB_PATH`（每测试独立）与假网络接缝（fake model outbound／拒绝式 updater transport），**在导入 `app.main` 之前**完成——防止模块级 `app = create_app()` 在导入期以真实库构造；② 再导入 `app.main`（只导入；不运行 lifespan、不启动服务）；③ 导入后替换 `app.main._start_scheduler_threads` 为假函数（计数、返回可 stop 假 event、不创建线程/不调用 startup/tick；保留至 teardown）；④ 然后显式构造（`create_app()`）并启动**被注入的那个 app 对象**；⑤ **禁止**用 uvicorn import-string 另起未注入的新实例（如 `uvicorn app.main:app`），也不得先导入 main 再设 env 或复用模块级 app。
2. 注入面：假 `app.state.model_outbound` 与可计数 availability/network seam；对真实 updater/model transport 设“调用即记录并失败”的拒绝假件；唯一临时 DB 路径须断言。
3. 断言：线程启动 seam 命中、真实出站计数＝0、临时 DB 路径正确、浏览器仅允许本 loopback origin；仅替换 `app.state.update_scheduler` 不算隔离证据。
4. M4 范围＝受影响 UI 结构测试＋真实 Chromium e2e；**Chromium 缺失＝blocked**，skip 不计通过；命令用 §2 唯一 basetemp。
5. 现 e2e fixture 直接 `create_app()` 且未按序隔离；Red 前必须按本节①–⑤改造（见 §4.4）。
6. 确定性：跨标签页 409、延迟/重排 ACK、clear epoch 均用显式同步点，不用 sleep 推测。
7. **UI 七场景（计划，来自 ui-singleflight；REQ-11）**：`test_ui_singleflight_rapid_edits_delayed_invalidate_ack`、`test_ui_singleflight_edit_during_prepare_error_ack_reordering`、`test_ui_singleflight_cross_tab_409_explicit_resync`、`test_ui_singleflight_clear_session_epoch_drops_late_replies`、`test_ui_salary_nomination_actual_hypothetical_and_conditional_copy`、`test_ui_child_dda_nomination_not_spouse_dda`、`test_ui_singleflight_focus_consent_no_auto_outbound`。期望 locator/行为：004 §3.6/§7 的 nomination 控件 id 与单一 mutation 队列；条件式文案“条件式比较，非已接纳税额”可见且不得进入当前税额/best；均未标记通过。

## 7. 发布证明计划（B5；替换通用行；全部 PLANNED）

### 7.1 八条路径的实际触发、独立来源与检查点

| case / 路径 | 实际消费者与精准触发输入 | 独立方法、检查点与失败边界 |
| --- | --- | --- |
| `a_avd_related_fixed`／`stamps.property.related_conveyance.fixed_duty` | `app/engines/stamps/property.py:134–145,265–288`；API `app/api/validation.py:1097,1116–1142`；输入 2025-06-10、conveyance_on_sale、consideration=value=5,000,000、prior_links.conform `{agreement_date:"2025-03-01",agreement_duly_stamped:{value:true}}` | 已批 D1.7、Cap117 s.29D(2)(a)、sd_pty_rates Note(ii)；检查进入 fixed 链、结果＝独立核实固定额（现基线 100），**不是** 112,500 的 AVD 表。普通已盖章链有真实 reader，**不得误称整类 excluded**；未盖章/特殊链保持拒算 |
| `a_stock_other_fixed`／`stamps.stock.other_transfer_fixed` | `app/engines/stamps/stock.py:88,125–128`；2025-06-10、complete=true、两份 other_transfer 文书、不附从价金额 | D2.1、Cap117 head2、GovHK 对应原文；逐份 ceil(F)、合计 2×ceil(F)，现基线 10。普通 contract-note 输入不构成该路径证据 |
| `a_lease_ceiling_boundary`／`stamps.lease.rent_ceiling_unit` | `app/engines/stamps/lease.py:123–125,356–359`；2025-06-10、indefinite、annual_rent、premium=0、copies=0；年租 10,000／10,000.01／10,099.99／10,100 子 case | D3.1/D3.1a、Cap117 head1(2)；独立整数商余 `ceil(R/U)×U` 再 ceil 税；U=100 时基数 10000/10100/10100/10100、税 25/26/26/26；至少一个非整倍数输入使参数作用可观察 |
| `a_stock_effective_boundary`／`stamps.stock.effective_from` | reader 在 `app/engines/stamps/stock.py:168–173`，当前法定起点 2023-11-17 在产品窗口之前 | **无窗口内前后成功 witness 且 date_string 未获 classifier 支持 → 候选 quarantine，报告 `coverage_gap`**；不得移日期至 2025 凑边界，不得以“税额未变”证明变更安全，不新增 reader/类型支持 |
| `a_property_rate`／`property.years.2025_26.standard_rate` | `app/engines/property.py:61,107`；2025_26 单物业 rent=120000，差饷/坏账/抵销 0 | 已批 D5、pty.htm Q7/Cap112 s5；独立 NAV=96000，再 floor(NAV×独立 rate)，基线 14400；rational_string 未获发布支持 → 隔离不等于消费者不存在 |
| `a_property_repair_ratio`／`property.repair_allowance_ratio` | `app/engines/property.py:62,107`；rent=120000 与 120000.99，其他扣减 0，逐三个支持年度 | D5、BIR57 Note3、Cap112 s5(1A)；先独立 floor(R×(1−q)) 再 floor 税；缺年度证明或类型不支持 → 隔离 |
| `a_profits_threshold_boundary`／`profits.two_tier_threshold` | `app/engines/profits.py:103–104,173–189,432–434`；2025_26 独资、loss=0、two_tier `{connected_entities:[],election_made:{value:true},no_other_election_same_year:{value:true}}`；利润 1,999,999.99／2,000,000／2,000,000.01／2,000,001 | D4.1–D4.3 与 D6.2a 取整；独立检查 floor 择档、低/高档税分量、before、rebate、final，不引用生产 `_bracket_tax`；独立 T1 参数证据不足 → 隔离，不升级 T2 |
| `a_profits_rebate_cap`／`profits.reductions.2025_26.cap` | `app/engines/profits.py:274–284,439–440`；同一合资格独资，利润 39,999.99／40,000／40,020 | 已批附表 43 与 D6.3；独立 before=2999/3000/3001，基线 cap 3000 时 reduction=2999/3000/3000、final=0/0/1；核对每业务一次 cap，不使用生产 `_reduction` |

- 直接 engine 可读某参数 ≠ 正式 API 支持；case 必须记录适用窗口、正式输入校验与消费者均可达。`main.py` 中的 “confirmed” 字符串本身不是独立证据；新参数与既有证据不一致时，旧基线 case 不能批准它。
- **B5 子 case 澄清**：八行中的历史数值是**基线示例**，不是新参数的固定 witness；对变化的 threshold/cap/ceiling/rate，须由**独立已核参数胶囊**（禁止 candidate.data/生产 helper）构造 proof-controlled 子 case，实际跨过新适用阈值/cap/取整边界，且至少一个分支/阶段影响可观察；static baseline 不得批准变化参数；缺合法 in-scope witness → `coverage_gap`/quarantine；新增 caps 的 0 边界仅在合法有意义域构造（**不伪造负输入**）；固定 case ID 不变，subcase 的 input/worksheet/阶段期望 hash 绑定 candidate/report。

### 7.2 候选输入、实测隔离与失败负例

- 每 case 固定登记 `{case_id,subcase_ids,input_proof_hash,required_read_paths,source_refs,worksheet_hash,expected_stages}`；引擎接收完整候选、不替换 data；run 前后重算完整 hash；read receipt 只证明读取，须匹配指定分支与独立阶段结果；static regression 与 affected case 独立列示；无 in-scope witness → `coverage_gap`/quarantine（如 stock effective_from）。
- **上下文局部可信同步观测作用域（不是 Python 沙箱）**：之前建立唯一临时目录并校验 SQLite 路径、登记 DB、读取 pointer/publications 基线；期间计数并拒绝网络/socket/DNS（含绕过 outbound 直连 HTTPX）与 SessionRegistry 会话创建；观察 SQL 写入（含 write-restore）；之后与 finally 重读全部实际 DB、核对写尝试日志、candidate hash、case/subcase 执行身份、包装复原；counter 来自观察器，缺失/异常不得填零通过；非 gate 调用委托原实现；**gate 内禁止新线程/子进程（防止 ContextVar 观测上下文逃逸）；本作用域不是通用 Python 沙箱，只沿用本节既有机制，不新建框架/端点/CLI**。若无法覆盖所选直接出口 → 保持 quarantine。
- 负例（计划）：`test_002_gate_rejects_wrong_candidate_hash`、`::test_002_gate_rejects_report_reuse`、`::test_002_gate_recomputes_required_set`、`::test_002_gate_detects_candidate_data_replacement`、`::test_002_gate_rejects_uncovered_changed_path`、`::test_002_gate_detects_pointer_write_then_restore`、`::test_002_gate_counts_blocked_direct_httpx_attempt`、`::test_002_gate_detects_session_creation`、`::test_002_gate_rejects_outside_temp_db`、`::test_002_gate_observers_restore_on_exception`、`::test_002_gate_observers_do_not_block_non_gate_context`、`::test_002_seed_null_baseline_empty_cas`、`::test_002_publish_baseline_changed_no_partial_write`、`::test_002_resolution_baseline_change_cannot_reuse`。

### 7.3 seed/update 判别与 CAS

- 报告顶层 `purpose:"seed"|"update"`；`baseline_bundle_id/hash` nullable：update 非 null（按实际 current 校验生成）、seed 均 null（发行固定 candidate/hash/profile＋绑定验证产物）；单个 null/未知 purpose/不符组合拒绝；required 集合按 purpose/diff 服务器重算，报告不能自减。
- publisher 事务内：update 复核 current 指针＝预期 baseline 且内容 hash/schema 兼容；seed 复核 `current_pointer` 无行且 `rule_bundles` 零行且无损坏；再匹配报告 candidate/hash/profile/proof；任何不符零切换；禁止任意 AppError 触发 seed。P1/P2 注入 seed ≠ 生产 bootstrap 批准（P3 发行产物）。

## 8. 未运行与限制

- 全部测试/浏览器/报告/观测命令均为计划，未运行；无运行时/金额/隔离证据。005 示例与 P/digest 仅做过文档级 stdlib 解析/哈希一致性抽查（见 `sdd-revision-r4.md`），非 pytest、非生产证据。
- 本清单覆盖命名范围；不构成行为证明，不称“全部运行关闭”；**原 council NEEDS REVISION 保留为历史；第五稿正式审查 APPROVED、有界 council READY、最后 P0 attempt3/3 GATE READY 与用户 Gate 2（2026-10-14，仅 stage1）已归并；未实现，Red/验收证据仍待创建**。
- 用户 Gate 2 已批准（2026-10-14，仅 stage1）；任何“已验证/已通过”表述仍须实际运行证据。
