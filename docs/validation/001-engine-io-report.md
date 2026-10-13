# 引擎 vs 官方示例 I/O 测试报告（001）

- 报告日期：2026-10-13
- 生成命令：`.venv/Scripts/python.exe scripts/generate_validation_report.py`
- 运行时环境：Python 3.13.14（`.venv/Scripts/python.exe`；Windows 11）
- 规则束：`app.rules.bundle.INITIAL_BUNDLE_CONTENT`（只读；rules_schema_version=1.0.0；适用年度=2024_25、2025_26、2026_27；印花税文书窗口=2024-04-01..2026-10-07）
- 运行性质：本地只读、不联网；表中「引擎实际输出」均为本脚本本次真实运行结果；输入形状逐字段照抄 tests/engines/** 与 tests/acceptance/** 既有用例语义。

## 批次 A：官方示例对照

口径说明：官方期望值列附出处锚（台账条目／官方文件／测试注释手算）；「未覆盖」＝测试库无对应用例锚，不虚构输入与期望值。

| 案例 | 输入摘要 | 官方期望值（出处锚） | 引擎实际输出（步骤/金额） | 一致？ | 对应测试名 |
| --- | --- | --- | --- | --- | --- |
| PAM39 Q1（2025/26） | 薪俸：240,000／MPF 9,000／single（无暂缴事实） | 累进 3,940、宽减 3,000、final 940（T2 PAM39 Q1，台账 1.13；T1 附表 2/4/43 手算见测试注释） | status=partial；before_reduction=3940；reduction=3000；final_after_reduction=940 | ✅ 一致 | test_salaries_pam39_q1_final_940 |
| PAM39 Q2 PST（2026/27） | 薪俸：480,000／MPF 18,000／single（无暂缴事实） | PST 35,890；2026/27 无宽减条目 → reduction 0、final 35,890（T2 PAM39 Q2，台账 1.13） | status=partial；before_reduction=35890；reduction=0；final_after_reduction=35890；next_provisional=35890 | ✅ 一致 | test_salaries_pam39_pst_2026_27_35890 |
| 物业 Q7（2025/26） | 单一物业：租金 120,000、差饷 0、无不可追回/按金 | NAV 96,000 → 税 14,400＋下年度暂缴 14,400（pty.htm Q7，T1 台账 1.7；无物业税宽减） | status=complete；nav=96000；tax=14400；before_reduction=14400；reduction=0；final_after_reduction=14400；next_provisional=14400 | ✅ 一致 | test_property_q7_120000_96000_14400 |
| PA Q32（2025/26） | 已婚两人：本人薪金 250,000；配偶物业租金 240,000（NAV 192,000） | PA 9,920 vs 分开评税 28,800＋0（Q32，T1 台账 1.7） | PA：before=12920、reduction=3000、final=9920；no_pa：final=28800（salaries=0、property=28800）；best=personal_assessment | ✅ 一致 | test_pa_q32_example_9920_vs_28800 |
| PAM37 例 4 | 已婚两人：本人独资 480,000；配偶租金 150,000（NAV 120,000） | R 600,000；累进 39,120；宽减 3,000；final 36,120；内部比例 4/5、1/5（PAM37 例 4，T2 台账 1.13；个人整元不作产品输出） | PA：before=39120、reduction=3000、final=36120；ratios={"self": "4/5", "spouse": "1/5"} | ✅ 一致 | test_pa_pam37_example4_joint_rebate_then_ratio_apportion_internal_fractions |
| PAM37 例 1 | （未提供） | （测试库未覆盖 PAM37 例 1/2/3，不虚构输入与期望值） | （未运行） | 未覆盖 | （未覆盖） |
| PAM37 例 2 | （未提供） | （测试库未覆盖 PAM37 例 1/2/3，不虚构输入与期望值） | （未运行） | 未覆盖 | （未覆盖） |
| PAM37 例 3 | （未提供） | （测试库未覆盖 PAM37 例 1/2/3，不虚构输入与期望值） | （未运行） | 未覆盖 | （未覆盖） |
| 合伙 Q7（2025/26） | 混合合伙：利润 5,000,000；法团 1/5＋个人 2/5＋2/5 | 业务层税额 612,000（33,000＋120,000＋99,000＋360,000）；宽减 3,000 → 609,000；门槛按 partner_kind 聚合（T2 2trexample Q7 结构例） | before=612000、reduction=3000、final=609000；corp：threshold=400000、low=33000、high=99000；individual：threshold=1600000、low=120000、high=360000 | ✅ 一致 | test_partnership_q7_612000 |
| 合伙 Q8（2026/27） | 合伙：利润 3,500,000、承前亏损 700,000、五名个人各 1/5、PA 选择 p-0 | 抵亏后 2,800,000 → 业务税 270,000；PA 相关税 54,000；余额 216,000；暂缴按抵亏前 375,000（T2 2trexample Q8 结构例） | before=270000、reduction=0、final=270000；PA 相关税=54000、余额=216000；next_provisional=375000 | ✅ 一致 | test_partnership_q8_loss_offset_then_pa_transfer_270000_54000_216000 |
| 租约 ≤1 年：60,000→150 | fixed 2025-07-01..2025-12-31、total_rent 60,000、premium 0 | ≤1 年档 0.25%×总租金 60,000＝150（T1 档位＋ceil100/ceil1，T1-observed 周年日；T3 手算） | total=150；rounded_rent_base=60000；rent_duty=150；premium_duty=0 | ✅ 一致 | test_lease_six_months_uses_total_not_annual_rent |
| 租约 >1 年年化：252,000→630 | fixed 2025-07-01..2027-06-30（恰 2 年）、total_rent 252,000 | 平均年租 126,000（round4）→ >1–3 年档 0.5% → 630（T1-observed 年化；T3 手算） | total=630；rounded_rent_base=126000；rent_duty=630 | ✅ 一致 | test_lease_two_year_variable_rent_uses_average |
| 租约 indefinite：120,000→300 | indefinite、annual_rent 120,000、无 term_end | 未界定期限档 0.25%×年租 120,000＝300（T1 档位＝有效档非缺失；T3 手算） | total=300；rounded_rent_base=120000；rent_duty=300 | ✅ 一致 | test_lease_indefinite_is_valid_not_missing_term |
| 租约 3 年+1 天：120,000→1,200 | fixed 2025-07-01..2028-07-01（3 年 1 天，起讫两日都算）、月租 10,000 | 3 年+1 天＝超过 3 年档 1%×年租 120,000＝1,200（T1 档位；T3 手算） | total=1200；rounded_rent_base=120000；rent_duty=1200 | ✅ 一致 | test_lease_exact_one_and_three_year_boundaries |
| AVD 3,000,000.01→101 | B=3,000,000.01、2024-06-10（Table 8） | (3m,3,528,240] 档 100＋10%×0.01=100.001 → 税额 ceil1=101（T3 手算；表值 T1） | base_amount=3000000.01；duty=101；status=complete | ✅ 一致 | test_avd_table8_historical_window_2024 |
| AVD 3,528,240→52,924 | B=3,528,240、2024-06-10（Table 8 桥档上端） | 100＋10%×(3,528,240−3,000,000)=52,924（T3 手算；表值 T1） | base_amount=3528240；duty=52924；status=complete | ✅ 一致 | test_avd_table8_historical_window_2024 |
| AVD 4,323,780→64,856 | B=4,323,780、2025-06-10（Table 9 桥档上端） | 100＋20%×(4,323,780−4,000,000)=64,856（T3 手算；表值 T1） | base_amount=4323780；duty=64856；status=complete | ✅ 一致 | test_avd_table9_2025_window |
| AVD 4,323,780.01→64,857 | B=4,323,780.01、2025-06-10（Table 9 换档点，下开上闭） | 1.5%×4,323,780.01=64,856.70015 → ceil1=64,857（区间定义，非矛盾；T3 手算） | base_amount=4323780.01；duty=64857；status=complete | ✅ 一致 | test_avd_table9_2025_window |
| AVD 100,000,000→4,250,000 | B=100,000,000、2026-06-10、residential（2026 住宅高端档上端） | (21,739,120,100m] 档 4.25%×1e8=4,250,000（T3 手算；Ord 3/2026 表值 T1） | base_amount=100000000；duty=4250000；status=complete | ✅ 一致 | test_avd_2026_residential_high_bands |
| AVD 109,574,470→7,122,341 | B=109,574,470、2026-06-10、residential（30% 档上端） | 4,250,000＋30%×9,574,470=7,122,341（T3 手算；Ord 3/2026 表值 T1） | base_amount=109574470；duty=7122341；status=complete | ✅ 一致 | test_avd_2026_residential_high_bands |
| AVD 基数不取整至 100（4,000,000.01→101） | B=max(4,000,000.01, 4,000,000)=4,000,000.01、2025-06-10 | 100＋20%×0.01=100.002 → ceil1=101；基数不进位至 100（D1.2；T3 手算） | base_amount=4000000.01；duty=101；status=complete | ✅ 一致 | test_avd_property_base_not_rounded_to_100 |
| 股票 每 note 分别 ceil1→4 | sold note 1,000.01＋bought note 1,000.01、complete=true | 每份 0.1%=1.00001 → 各 ceil1=2；sold 2＋bought 2=4（不合并、不互摊；T1 head 2＋T3 手算） | total=4；documents=[{"doc_kind": "contract_note_sold", "duty": "2"}, {"doc_kind": "contract_note_bought", "duty": "2"}] | ✅ 一致 | test_stock_sold_and_bought_notes_ceil_separately |
| 股票 自愿处置→8 | voluntary_inter_vivos、value 1,000.01 | $5＋0.2%×1,000.01=7.00002 → 一份文书一次 ceil1=8（T1 head 2；T3 手算） | total=8；documents=[{"doc_kind": "voluntary_inter_vivos", "duty": "8"}] | ✅ 一致 | test_stock_voluntary_transfer_fixed_plus_value_duty |
| 股票 其他转让→5 | other_transfer、consideration 1,000,000（有代价也不套从价率） | $5 每份（不得替代一般买卖从价税；T1 head 2；T3 手算） | total=5；documents=[{"doc_kind": "other_transfer", "duty": "5"}] | ✅ 一致 | test_stock_other_transfer_is_5_per_document |

## 批次 B：模拟数据（固定 seed）

> ⚠️ **模拟数据**：以下 20 条由 `random.seed(20261013)` 生成，仅演示引擎 I/O 与状态/金额字段形态，**不构成任何合规或官方示例样例**；不得作为计算正确性证据引用。

| 引擎 | 输入 JSON（截断） | 状态 | 关键金额 |
| --- | --- | --- | --- |
| 薪俸税（salaries_tax） | `{"employment_income":"1992000","married_status":{"value":"single"},"mpf_mandatory_contributions":"4900","year_of_assessment":"2026_27"}` | partial | before_reduction=295157；reduction=0；final_after_reduction=295157；provisional_paid=null；next_provisional=295157；balance=null |
| 薪俸税（salaries_tax） | `{"employment_income":"1463000","married_status":{"value":"single"},"mpf_mandatory_contributions":"12100","provisional_paid":"39500","year_of_assessment":"2026_27"}` | complete | before_reduction=204003；reduction=0；final_after_reduction=204003；provisional_paid=39500；next_provisional=204003；balance=164503 |
| 薪俸税（salaries_tax） | `{"employment_income":"338000","married_status":{"value":"single"},"mpf_mandatory_contributions":"18400","year_of_assessment":"2026_27"}` | partial | before_reduction=12500；reduction=0；final_after_reduction=12500；provisional_paid=null；next_provisional=12500；balance=null |
| 薪俸税（salaries_tax） | `{"employment_income":"421000","married_status":{"value":"single"},"mpf_mandatory_contributions":"400","provisional_paid":"46500","year_of_assessment":"2026_27"}` | complete | before_reduction=28852；reduction=0；final_after_reduction=28852；provisional_paid=46500；next_provisional=28852；balance=-17648 |
| 薪俸税（salaries_tax） | `{"employment_income":"2397000","married_status":{"value":"single"},"mpf_mandatory_contributions":"2600","year_of_assessment":"2026_27"}` | partial | before_reduction=359160；reduction=0；final_after_reduction=359160；provisional_paid=null；next_provisional=359160；balance=null |
| 薪俸税（salaries_tax） | `{"employment_income":"648000","married_status":{"value":"single"},"mpf_mandatory_contributions":"5100","provisional_paid":"46500","year_of_assessment":"2026_27"}` | complete | before_reduction=66643；reduction=0；final_after_reduction=66643；provisional_paid=46500；next_provisional=66643；balance=20143 |
| 薪俸税（salaries_tax） | `{"employment_income":"311000","married_status":{"value":"single"},"mpf_mandatory_contributions":"3500","year_of_assessment":"2024_25"}` | partial | before_reduction=12570；reduction=1500；final_after_reduction=11070；provisional_paid=null；next_provisional=12570；balance=null |
| 薪俸税（salaries_tax） | `{"employment_income":"858000","married_status":{"value":"single"},"mpf_mandatory_contributions":"14900","year_of_assessment":"2024_25"}` | partial | before_reduction=102887；reduction=1500；final_after_reduction=101387；provisional_paid=null；next_provisional=102887；balance=null |
| 利得税（profits_tax） | `{"assessable_profit":"2766000","entity_kind":"sole_proprietorship","two_tier":{"connected_entities":[],"election_made":{"value":true},"no_other_election_same_year":{"value":true}},"year_of_assessment…` | complete | before_reduction=264900；reduction=0；final_after_reduction=264900；provisional_paid=null；next_provisional=264900；balance=null |
| 利得税（profits_tax） | `{"assessable_profit":"3518000","entity_kind":"sole_proprietorship","two_tier":{"connected_entities":[],"election_made":{"value":true},"no_other_election_same_year":{"value":true}},"year_of_assessment…` | complete | before_reduction=377700；reduction=3000；final_after_reduction=374700；provisional_paid=null；next_provisional=377700；balance=null |
| 利得税（profits_tax） | `{"assessable_profit":"2880000","entity_kind":"corporation","two_tier":{"connected_entities":[],"election_made":{"value":true},"no_other_election_same_year":{"value":true}},"year_of_assessment":"2026_…` | complete | before_reduction=310200；reduction=0；final_after_reduction=310200；provisional_paid=null；next_provisional=310200；balance=null |
| 利得税（profits_tax） | `{"assessable_profit":"3950000","entity_kind":"sole_proprietorship","two_tier":{"connected_entities":[],"election_made":{"value":true},"no_other_election_same_year":{"value":true}},"year_of_assessment…` | complete | before_reduction=442500；reduction=0；final_after_reduction=442500；provisional_paid=null；next_provisional=442500；balance=null |
| 利得税（profits_tax） | `{"assessable_profit":"4695000","entity_kind":"sole_proprietorship","two_tier":{"connected_entities":[],"election_made":{"value":true},"no_other_election_same_year":{"value":true}},"year_of_assessment…` | complete | before_reduction=554250；reduction=0；final_after_reduction=554250；provisional_paid=null；next_provisional=554250；balance=null |
| 利得税（profits_tax） | `{"assessable_profit":"3993000","entity_kind":"corporation","two_tier":{"connected_entities":[],"election_made":{"value":true},"no_other_election_same_year":{"value":true}},"year_of_assessment":"2025_…` | complete | before_reduction=493845；reduction=3000；final_after_reduction=490845；provisional_paid=null；next_provisional=493845；balance=null |
| 物业税（property_tax） | `{"properties":[{"deposit_offsets":"0","irrecoverable_rent":"0","property_id":"sim-0","rates_paid_by_owner":{"actually_paid":{"value":true},"agreed":{"value":true},"amount":"0"},"rent_received":"52900…` | complete | before_reduction=119280；reduction=0；final_after_reduction=119280；provisional_paid=null；next_provisional=119280；balance=null |
| 物业税（property_tax） | `{"properties":[{"deposit_offsets":"0","irrecoverable_rent":"0","property_id":"sim-0","rates_paid_by_owner":{"actually_paid":{"value":true},"agreed":{"value":true},"amount":"0"},"rent_received":"37300…` | complete | before_reduction=78480；reduction=0；final_after_reduction=78480；provisional_paid=null；next_provisional=78480；balance=null |
| 物业税（property_tax） | `{"properties":[{"deposit_offsets":"0","irrecoverable_rent":"0","property_id":"sim-0","rates_paid_by_owner":{"actually_paid":{"value":true},"agreed":{"value":true},"amount":"0"},"rent_received":"30100…` | complete | before_reduction=55800；reduction=0；final_after_reduction=55800；provisional_paid=null；next_provisional=55800；balance=null |
| 物业税（property_tax） | `{"properties":[{"deposit_offsets":"0","irrecoverable_rent":"0","property_id":"sim-0","rates_paid_by_owner":{"actually_paid":{"value":true},"agreed":{"value":true},"amount":"0"},"rent_received":"10700…` | complete | before_reduction=161280；reduction=0；final_after_reduction=161280；provisional_paid=null；next_provisional=161280；balance=null |
| 物业税（property_tax） | `{"properties":[{"deposit_offsets":"0","irrecoverable_rent":"0","property_id":"sim-0","rates_paid_by_owner":{"actually_paid":{"value":true},"agreed":{"value":true},"amount":"0"},"rent_received":"54300…` | complete | before_reduction=65160；reduction=0；final_after_reduction=65160；provisional_paid=null；next_provisional=65160；balance=null |
| 物业税（property_tax） | `{"properties":[{"deposit_offsets":"0","irrecoverable_rent":"0","property_id":"sim-0","rates_paid_by_owner":{"actually_paid":{"value":true},"agreed":{"value":true},"amount":"0"},"rent_received":"20400…` | complete | before_reduction=96600；reduction=0；final_after_reduction=96600；provisional_paid=null；next_provisional=96600；balance=null |

## 批次 C：边界与三态

| 检查项 | 输入摘要 | 期望行为（依据） | 引擎实际输出 | 一致？ | 对应测试名 |
| --- | --- | --- | --- | --- | --- |
| C1 独资净利 2,000,000.01（低档全率） | profits：sole_proprietorship、assessable_profit 2,000,000.01、2026/27 | 择档 floor(2,000,000.01)=2,000,000 ≤ 2m → 低档全率 150,000；final 150,000；非法团非产品约定（D6.2a 计算器观察） | before=150000、reduction=0、final=150000；pending_verification 无「产品约定」标识 | ✅ 一致 | test_profits_unincorporated_final_floor_three_year_calculator_convention |
| C2 物业两笔 AV 各 17（逐物业 vs 聚合） | property：p-a 与 p-b 各 17（NAV raw 13.6） | 逐物业 floor(13.6)=13 → 税 floor(1.95)=1；合计 2；聚合一次取整反例 4 不得采用（D5.0） | properties.tax=["1", "1"]；before=2（聚合反例 4 未采用） | ✅ 一致 | test_property_aggregate_vs_per_property_rounding_not_conflated |
| C3 缺 provisional_paid（partial / balance null） | PA：Q32 同款事实但删除 provisional_paid 键（缺失 ≠ 显式零） | status=partial；已知 final 9,920；provisional_paid/balance=null；missing_components 含 provisional_paid（REQ-8） | status=partial；final_after_reduction=9920；provisional_paid=null；balance=null；missing_components=["provisional_paid"] | ✅ 一致 | test_missing_pst_partial_no_total_balance |
| C4 不支持扣除正数（E_DEDUCTION_UNSUPPORTED） | 物业 ground_rent 5,000；薪俸 voluntary_mpf_contribution 5,000 | 两引擎均抛 AppError(code=E_DEDUCTION_UNSUPPORTED)，不得静默忽略（REQ-10） | 物业引擎：E_DEDUCTION_UNSUPPORTED；薪俸引擎：E_DEDUCTION_UNSUPPORTED | ✅ 一致 | test_property_rejects_ground_rent_repairs_insurance_interest, test_acceptance_unsupported_deduction_positive_rejected |
| C5 资格 unknown（needs_input） | 薪俸：married_status.value=unknown（240,000／MPF 9,000） | status=needs_input；中文 questions 非空；amounts 六键一律显式 None（不填零、不部分计算） | status=needs_input；questions=1 条；amounts=全 None | ✅ 一致 | test_salaries_unknown_eligibility_questions |
| C6 印花税文书日期超窗（E_PERIOD_NOT_SUPPORTED） | AVD：instrument_date 2026-10-08（窗口至 2026-10-07） | AppError(code=E_PERIOD_NOT_SUPPORTED)（bundle.applicability.stamp_duty_instrument_window；无直接引擎测试，本脚本实跑核验） | calculate_property_avd → E_PERIOD_NOT_SUPPORTED | ✅ 一致 | （无直接引擎测试） |
| C7 tax_type=[]（API 层结构化拒绝） | prepare 请求体 tax_type=[]（非法 JSON 类型；引擎层不适用——引擎契约输入为 facts Mapping，无 tax_type 字段） | API 层结构化 422 E_INPUT_TYPE（field=tax_type），不得未处理 TypeError；由 tests/acceptance 保障 | validate_prepare_body → E_INPUT_TYPE（引擎层不适用：由 API 层保障） | ✅ 一致 | test_acceptance_tax_type_wrong_shape_structured_rejection |

## 批次 D：汇总

- 对照项合计：31（批次 A 24＋批次 C 7；批次 B 20 条模拟数据不计入对照）
- 一致：28
- 不一致：0
- 未覆盖：3
- 脚本退出码：0（无不一致 → 0；任一不一致 → 1）

未覆盖清单（如实标注，不得虚构）：
- PAM37 例 1：（测试库未覆盖 PAM37 例 1/2/3，不虚构输入与期望值）
- PAM37 例 2：（测试库未覆盖 PAM37 例 1/2/3，不虚构输入与期望值）
- PAM37 例 3：（测试库未覆盖 PAM37 例 1/2/3，不虚构输入与期望值）
