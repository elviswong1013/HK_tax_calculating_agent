# 002 - 官方计算器实时驱动对比（Playwright，IRD/GovHK）

任务：用 Playwright 实时驱动 IRD/GovHK 官方网页计算器，与本仓库 app.engines 纯函数对同一输入的输出对照。

- **运行时间**：2026-10-13T20:33:27+08:00 ～ 2026-10-13T20:42:10+08:00（HKT）
- **环境**：Windows 10 + 本仓库 .venv（playwright 1.63 + chromium，无头模式）；网络仅访问官方域名 www.ird.gov.hk / www.gov.hk（外加解析真实 IP 用的 networkcalc.com DNS API）。
- **性质**：尽力而为的验证记录；每项如实标注 成功 / 受限，含全部失败尝试。
- **声明**：仅驱动官方 UI（填字段、点 Compute、读显示值），未修改任何官方页面逻辑；所有页面字节均在运行时从官方服务器实时取回。本目录与 screenshots/ 为本次新增，未改动仓库其他文件。

## 1. 薪俸税计算器（IRD stc.htm，三年度）

输入（三年度相同）：单身、受雇收入 240,000、MPF 强制供款 9,000，其余全部留 0（官方页面默认）。

stc.htm（薪俸税）为旧式 frameset 设计：initPage()→restorefrmIn() 依赖 frameset 提供的 parent.frmInStatus，DisplayResult() 跳转 parent.main.location.href=“eoutdis.htm”，单开时两者皆缺。处理方式＝最小 parent shim（任务预授权）：①先调用官方自身的 savefrmIn() 把『未填写的初始表单』快照进 parent.frmInStatus，再刷新页面让官方 initPage() 正常跑完（含 GetDeduction() 设定年度全局量）；②补一个空 <iframe name="main"> 作为官方结果页 eoutdis.htm 的落点。计算与结果渲染全部为官方原始代码，未修改任何页面逻辑；结果从该框架内官方渲染的可见文本读取。

### 1.1 2024/25（st_comp_2024_25/stc.htm）

- **URL**：https://www.ird.gov.hk/eng/ese/st_comp_2024_25/stc.htm
- **时间戳**：2026-10-13T20:33:27+08:00 → 2026-10-13T20:33:39+08:00（success）
- **输入步骤**：

1. goto https://www.ird.gov.hk/eng/ese/st_comp_2024_25/stc.htm (route-fulfilled with live official bytes; stc bootstrap init-script registered: official savefrmIn() snapshot before initPage, restoring frameset-provided frmInStatus)
2. inject minimal <iframe name=main> shim (frameset result-frame restoration)
3. select Single radio, fill employment income=240000, MPF mandatory=9000
4. click official Compute button (#ComputeButton, name=B1)
5. wait for official eoutdis.htm result page rendered inside the main frame

- **官方显示值原文**（官方 eoutdis.htm 结果框架 innerText 摘录）：

```text
Computation of Estimated Salaries Tax Liabilities
Year of assessment 2024/25
Based on the information entered, the amount of tax payable by you is computed as follows : -
 	HK$
TOTAL INCOME	240,000
DEDUCTIONS	9,000
ALLOWANCES:-
    Basic	132,000
    Single Parent	0
    Personal Disability	0
    Child(ren)	0
    Dependent Brother(s) / Sister(s)	0
    Dependent Parent(s) / Grandparent(s)
      - Aged 55 to 59	0
      - Aged 60 or over, or aged under 60 but disabled	0
    Disabled Dependant(s)	0
TOTAL ALLOWANCES	132,000
NET CHARGEABLE INCOME	99,000
TAX PAYABLE BY YOU (BEFORE TAX REDUCTION)	3,940
TAX REDUCTION	1,500
TAX PAYABLE BY YOU (AFTER TAX REDUCTION)	2,440
Disclaimer:
The amount of the estimated tax payable is computed according to the information that you have entered. It is for your reference only. IRD accepts no liability for any loss arising from or related to the use of the information. Upon examination of your tax return, the Assessor will make adjustments, if appropriate, in your tax assessment.
2017 | Important notices	Last revision date: 9 May 2025
```

- **官方显示解析**：before_reduction=3,940，reduction=1,500，final_after_reduction=2,440
- **本引擎值**（app.engines.salaries.calculate_salaries_tax，facts 形状同 tests/engines/test_salaries_engine.py）：status=partial，before=3,940，reduction=1,500，final=2,440
- **对照**：✅ 一致（final 口径；before/reduction 同表见 §5）
- **下年度暂缴（Provisional tax for the following year）官方显示**：（官方结果页无此分项，不虚构）
- **本引擎 next_provisional**（同输入 240k/9k/单身，按下一年度规则估算、不计宽减——附表 43 宽减不适用暂缴）：3,940
- **暂缴对照**：受限（官方结果页无暂缴分项，无从直接对照；官方字节逐字核验与间接核对见 §6）
- 观察：parsed from displayed rows: before_reduction=3940, final_after_reduction=2440
- 观察：official result page shows NO provisional-tax line item ('Provisional tax for the following year' absent from the rendered eoutdis.htm text; the page computes current-year tax only) - recorded as-is, not filled in
- 观察：no two-tiered standard-rate remark -> progressive-rate path chosen
- 截图：[screenshots/01_stc_2024_25_input.png](screenshots/01_stc_2024_25_input.png)
- 截图：[screenshots/01_stc_2024_25_result.png](screenshots/01_stc_2024_25_result.png)

### 1.2 2025/26（st_comp_2025_26/stc.htm）

- **URL**：https://www.ird.gov.hk/eng/ese/st_comp_2025_26/stc.htm
- **时间戳**：2026-10-13T20:33:39+08:00 → 2026-10-13T20:33:47+08:00（success）
- **输入步骤**：

1. goto https://www.ird.gov.hk/eng/ese/st_comp_2025_26/stc.htm (route-fulfilled with live official bytes; stc bootstrap init-script registered: official savefrmIn() snapshot before initPage, restoring frameset-provided frmInStatus)
2. inject minimal <iframe name=main> shim (frameset result-frame restoration)
3. select Single radio, fill employment income=240000, MPF mandatory=9000
4. click official Compute button (#ComputeButton, name=B1)
5. wait for official eoutdis.htm result page rendered inside the main frame

- **官方显示值原文**（官方 eoutdis.htm 结果框架 innerText 摘录）：

```text
Computation of Estimated Salaries Tax Liabilities
Year of assessment 2025/26
Based on the information entered, the amount of tax payable by you is computed as follows : -
 	HK$
TOTAL INCOME	240,000
DEDUCTIONS	9,000
ALLOWANCES:-
    Basic	132,000
    Single Parent	0
    Personal Disability	0
    Child(ren)	0
    Dependent Brother(s) / Sister(s)	0
    Dependent Parent(s) / Grandparent(s)
      - Aged 55 to 59	0
      - Aged 60 or over, or aged under 60 but disabled	0
    Disabled Dependant(s)	0
TOTAL ALLOWANCES	132,000
NET CHARGEABLE INCOME	99,000
TAX PAYABLE BY YOU (BEFORE TAX REDUCTION)	3,940
TAX REDUCTION	3,000
TAX PAYABLE BY YOU (AFTER TAX REDUCTION)	940
Disclaimer:
The amount of the estimated tax payable is computed according to the information that you have entered. It is for your reference only. IRD accepts no liability for any loss arising from or related to the use of the information. Upon examination of your tax return, the Assessor will make adjustments, if appropriate, in your tax assessment.
2017 | Important notices	Last revision date: 22 May 2026
```

- **官方显示解析**：before_reduction=3,940，reduction=3,000，final_after_reduction=940
- **本引擎值**（app.engines.salaries.calculate_salaries_tax，facts 形状同 tests/engines/test_salaries_engine.py）：status=partial，before=3,940，reduction=3,000，final=940
- **对照**：✅ 一致（final 口径；before/reduction 同表见 §5）
- **下年度暂缴（Provisional tax for the following year）官方显示**：（官方结果页无此分项，不虚构）
- **本引擎 next_provisional**（同输入 240k/9k/单身，按下一年度规则估算、不计宽减——附表 43 宽减不适用暂缴）：3,160
- **暂缴对照**：受限（官方结果页无暂缴分项，无从直接对照；官方字节逐字核验与间接核对见 §6）
- 观察：parsed from displayed rows: before_reduction=3940, final_after_reduction=940
- 观察：official result page shows NO provisional-tax line item ('Provisional tax for the following year' absent from the rendered eoutdis.htm text; the page computes current-year tax only) - recorded as-is, not filled in
- 观察：no two-tiered standard-rate remark -> progressive-rate path chosen
- 截图：[screenshots/02_stc_2025_26_input.png](screenshots/02_stc_2025_26_input.png)
- 截图：[screenshots/02_stc_2025_26_result.png](screenshots/02_stc_2025_26_result.png)

### 1.3 2026/27（st_comp_2026_27/stc.htm）

- **URL**：https://www.ird.gov.hk/eng/ese/st_comp_2026_27/stc.htm
- **时间戳**：2026-10-13T20:33:47+08:00 → 2026-10-13T20:33:58+08:00（success）
- **输入步骤**：

1. goto https://www.ird.gov.hk/eng/ese/st_comp_2026_27/stc.htm (route-fulfilled with live official bytes; stc bootstrap init-script registered: official savefrmIn() snapshot before initPage, restoring frameset-provided frmInStatus)
2. inject minimal <iframe name=main> shim (frameset result-frame restoration)
3. select Single radio, fill employment income=240000, MPF mandatory=9000
4. click official Compute button (#ComputeButton, name=B1)
5. wait for official eoutdis.htm result page rendered inside the main frame

- **官方显示值原文**（官方 eoutdis.htm 结果框架 innerText 摘录）：

```text
Computation of Estimated Salaries Tax Liabilities
Year of assessment 2026/27
Based on the information entered, the amount of tax payable by you is computed as follows : -
 	HK$
TOTAL INCOME	240,000
DEDUCTIONS	9,000
ALLOWANCES:-
    Basic	145,000
    Single Parent	0
    Personal Disability	0
    Child(ren)	0
    Dependent Brother(s) / Sister(s)	0
    Dependent Parent(s) / Grandparent(s)
      - Aged 55 to 59	0
      - Aged 60 or over, or aged under 60 but disabled	0
    Disabled Dependant(s)	0
TOTAL ALLOWANCES	145,000
NET CHARGEABLE INCOME	86,000
TAX PAYABLE BY YOU	3,160
Disclaimer:
The amount of the estimated tax payable is computed according to the information that you have entered. It is for your reference only. IRD accepts no liability for any loss arising from or related to the use of the information. Upon examination of your tax return, the Assessor will make adjustments, if appropriate, in your tax assessment.
2017 | Important notices	Last revision date: 22 May 2026
```

- **官方显示解析**：before_reduction=3,160，reduction=0，final_after_reduction=3,160
- **本引擎值**（app.engines.salaries.calculate_salaries_tax，facts 形状同 tests/engines/test_salaries_engine.py）：status=partial，before=3,160，reduction=0，final=3,160
- **对照**：✅ 一致（final 口径；before/reduction 同表见 §5）
- **下年度暂缴（Provisional tax for the following year）官方显示**：（官方结果页无此分项，不虚构）
- **本引擎 next_provisional**（同输入 240k/9k/单身，按下一年度规则估算、不计宽减——附表 43 宽减不适用暂缴）：3,160
- **暂缴对照**：受限（官方结果页无暂缴分项，无从直接对照；官方字节逐字核验与间接核对见 §6）
- 观察：official page displays a single 'TAX PAYABLE BY YOU' row (no reduction rows - no 2026/27 Schedule 43 entry); before=final=value, reduction=0
- 观察：parsed from displayed rows: before_reduction=3160, final_after_reduction=3160
- 观察：official result page shows NO provisional-tax line item ('Provisional tax for the following year' absent from the rendered eoutdis.htm text; the page computes current-year tax only) - recorded as-is, not filled in
- 观察：no two-tiered standard-rate remark -> progressive-rate path chosen
- 截图：[screenshots/03_stc_2026_27_input.png](screenshots/03_stc_2026_27_input.png)
- 截图：[screenshots/03_stc_2026_27_result.png](screenshots/03_stc_2026_27_result.png)

## 2. GovHK 印花税（物业从价 AVD）计算器

GovHK 印花税入口页本身不含计算表单，页面内链接指向 IRD sd_comp 计算器；本次按入口页链接进入 sdpt_s2_2026.htm（住宅 AVD，Scale 1 Part 1 ＋ Scale 2）。该官方计算器无『文书日期』输入，按其页面说明只计算现行印花税；2026-02-26 起的现行住宅档位在 ≤21,739,120 区间与 2025-02-26 起的 Table 9 完全同档，故案例②（2025-06-01，4,323,780）官方现行计算器的输出与 2025 制度下的期望值可比。该口径限制如实记录。

入口页驱动：https://www.gov.hk/en/residents/taxes/etax/services/stamp_duty_computation.htm（failed，2026-10-13T20:33:58+08:00）；点击页面内链接『Ad Valorem Stamp Duty (AVD) (Part 1 of Scale 1 and Scale 2) - residential property』进入 sdpt_s2_2026.htm。落地 URL 记录：。
入口页失败原因：`TimeoutError('Page.goto: Timeout 90000ms exceeded.\nCall log:\n  - navigating to "https://www.gov.hk/en/residents/taxes/etax/services/stamp_duty_computation.htm", waiting until "domcontentloaded"\n')`（后续案例改用直达计算器 URL，不影响计算器本身的驱动）

补充说明（入口页『failed』的实况定性）：该 GovHK 门户页的主文档已被传输层 200 实时抓回（§6.7 日志含该 GET 及字节数，与直连核验逐字节一致），失败发生在浏览器导航层——gov.hk 门户单页含 100+ 子资源，经无 SNI 传输层逐请求串行抓取，90 秒 goto 窗口内未全部加载完成；官方服务器本身可达且返回官方字节。计算器本体（ird.gov.hk 直达 URL）不受影响，本次照常驱动与对照。

### 2.1 案例①：2026-06-01 住宅 100,000,000（任务期望 4,250,000）

- **URL**：https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm
- **时间戳**：2026-10-13T20:37:54+08:00 → 2026-10-13T20:37:59+08:00（success）
- **输入步骤**：

1. goto https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm (calculator linked from the GovHK entry page)
2. fill 'Property price or value' = 100000000
3. click official Compute button (#button_compute, name=B1)

- **官方显示值原文**：`Stamp Duty Payable input[name=T2] displayed value: 4,250,000`
- **本引擎值**（app.engines.stamps.property.calculate_property_avd，facts 形状同 tests/engines/stamps/test_property_avd.py）：status=complete，duty=4,250,000，detail=instrument_detail={'base_amount': '100000000', 'duty': '4250000'}
- **对照**：✅ 一致
- 截图：[screenshots/04_avd_case1_input.png](screenshots/04_avd_case1_input.png)
- 截图：[screenshots/04_avd_case1_result.png](screenshots/04_avd_case1_result.png)

### 2.2 案例②：2025-06-01 住宅 4,323,780（任务期望 64,856）

- **URL**：https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm
- **时间戳**：2026-10-13T20:37:59+08:00 → 2026-10-13T20:38:08+08:00（success）
- **输入步骤**：

1. goto https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm (calculator linked from the GovHK entry page)
2. fill 'Property price or value' = 4323780
3. click official Compute button (#button_compute, name=B1)

- **官方显示值原文**：`Stamp Duty Payable input[name=T2] displayed value: 64,856`
- **本引擎值**（app.engines.stamps.property.calculate_property_avd，facts 形状同 tests/engines/stamps/test_property_avd.py）：status=complete，duty=64,856，detail=instrument_detail={'base_amount': '4323780', 'duty': '64856'}
- **对照**：✅ 一致
- 截图：[screenshots/05_avd_case2_input.png](screenshots/05_avd_case2_input.png)
- 截图：[screenshots/05_avd_case2_result.png](screenshots/05_avd_case2_result.png)

## 3. 租约印花税计算器（IRD csdta.htm）

官方租约计算器（csdta.htm，§1.14 台账 URL）自身约定：租期 ≤1 年必须输入『全期总租金』，月租输入会被页面弹窗拒绝（本次实测留证）。故官方驱动采用等价输入：总租金 60,000（＝月租 10,000 × 6 个月），租期 2025-07-01..2025-12-31；本引擎侧 facts 形状按 tests/engines/stamps/test_lease.py 的 rent_input_mode='total_rent' 同口径。

### 3.1 案例：月租 10,000 × 6 个月 → 总租金 60,000（任务期望 150）

- **URL**：https://www.ird.gov.hk/chi/ese/sd_comp/csdta.htm
- **时间戳**：2026-10-13T20:38:08+08:00 → 2026-10-13T20:38:14+08:00（success）
- **输入步骤**：

1. goto https://www.ird.gov.hk/chi/ese/sd_comp/csdta.htm (official IRD lease calculator; Chinese UI, utf-8)
2. first demonstrate the official convention: monthly-rent input for a <=1 year term is rejected by the page (monthly equivalent 10000)
3. drive the accepted path: Total Rent over whole term = 60000 (= monthly 10000 x 6 months)

- **官方显示值原文**：`Current Stamp Duty Payable input[name=T7] displayed value: 150`
- **本引擎值**（app.engines.stamps.lease.calculate_lease_stamp_duty，facts 形状同 tests/engines/stamps/test_lease.py）：status=complete，total=150，detail=duty_breakdown={'rounded_rent_base': '60000', 'rent_duty': '150', 'premium_duty': '0', 'duplicate_duty': '0', 'total': '150'}
- **对照**：✅ 一致
- 观察：official monthly-rent rejection dialog (<=1 year term): 因租期並未超過1年，你必須輸入租約期內的租金總額。
- 截图：[screenshots/06_lease_input.png](screenshots/06_lease_input.png)
- 截图：[screenshots/06_lease_result.png](screenshots/06_lease_result.png)

## 4. 股票转让印花税计算器（IRD sdshare.htm）——任务预期『不存在』被实时调研推翻

股票计算器调研留痕（2026-10-13，全部经本目录 transport 实时访问官方域名或公开检索）：①GovHK 印花税入口页 stamp_duty_computation.htm 链接枚举：仅住宅 AVD（sdpt_s2_2026.htm）、非住宅 AVD（sdpt_s3_2026.htm）、租约（sdta.htm）三个计算器链接，无股票；②公开检索（官方域名限定）命中 IRD 官方『Stamp Duty Computation - Share Transfer』（ird.gov.hk/eng/ese/sd_comp/sdshare.htm，Last revision date: 17 November 2023）与 GovHK eTax 入口页『Stamp Duty Computation (Share Transfer Document)』（gov.hk/.../share_transfer_computation.htm，页面内 'Share Transfer Document' 链接指向 sdshare.htm）；③两页本次运行均实时 200 抓取核验。**任务简报预期『官方股票计算器不存在』与实况不符，已据实修正：官方计算器公开存在，本次按『若存在 → 同法实时对比』分支执行，未使用任何第三方计算器。**

入口页驱动：https://www.gov.hk/en/residents/taxes/etax/services/share_transfer_computation.htm（failed，2026-10-13T20:38:14+08:00）；点击页面内链接『Share Transfer Document』进入 sdshare.htm。落地 URL 记录：。
入口页失败原因：`TimeoutError('Page.goto: Timeout 90000ms exceeded.\nCall log:\n  - navigating to "https://www.gov.hk/en/residents/taxes/etax/services/share_transfer_computation.htm", waiting until "domcontentloaded"\n')`（后续案例改用直达计算器 URL，不影响计算器本身的驱动）

补充说明（入口页『failed』的实况定性）：该 GovHK 门户页的主文档已被传输层 200 实时抓回（§6.7 日志含该 GET 及字节数，与直连核验逐字节一致），失败发生在浏览器导航层——gov.hk 门户单页含 100+ 子资源，经无 SNI 传输层逐请求串行抓取，90 秒 goto 窗口内未全部加载完成；官方服务器本身可达且返回官方字节。计算器本体（ird.gov.hk 直达 URL）不受影响，本次照常驱动与对照。

口径差异（如实记录，非不一致）：官方股票计算器按『交易视图』显示一次买卖的印花税＝ BNSN（两份 contract note 合计，页面源码公式 bnsnValue=Math.ceil(0.0010×consideration)×2） ＋ IOT（转让文书固定 $5，恒显示）。本引擎按 D2.2 红线逐份文书计税：contract note 各 0.1% 分别 ceil1、sold/bought 不合并；**不得**在无 other_transfer 文书事实时自动附加转让文书 $5。故对照按两组口径映射列示：『官方 BNSN ↔ 引擎两 note 合计』、『官方 IOT ↔ 引擎 other_transfer 单文书（$5）』；自愿/非买卖路径官方 oIOT=5+Math.ceil(0.0020×value) 与引擎 voluntary_inter_vivos（$5＋0.2% of value，一次 ceil1）同口径直比。

### 4.1 案例①（买卖路径）：代价 1,000.01 → 官方 BNSN=各 note 0.1% 分别 ceil1 ×2（任务期望两 note 合计 4；另含 IOT $5）

- **URL**：https://www.ird.gov.hk/eng/ese/sd_comp/sdshare.htm
- **时间戳**：2026-10-13T20:42:05+08:00 → 2026-10-13T20:42:08+08:00（success）
- **输入步骤**：

1. goto https://www.ird.gov.hk/eng/ese/sd_comp/sdshare.htm (calculator linked from the GovHK eTax share-transfer entry page)
2. select 'Transfer of share by way of sale or purchase', fill Consideration = 1000.01
3. click official Compute button (input[value=Compute], onclick=Compute())

- **官方显示值原文**：`Instrument of Transfer input[name=IOT] displayed value: 5; Bought and sold note input[name=BNSN] displayed value: 4`
- **本引擎值**（app.engines.stamps.stock.calculate_stock_stamp_duty，facts 形状同 tests/engines/stamps/test_stock.py）：status=complete，total=4，detail=documents=[{'doc_kind': 'contract_note_sold', 'duty': '2'}, {'doc_kind': 'contract_note_bought', 'duty': '2'}]
- **对照**（官方值口径见各行观察）：✅ 一致
- 观察：official page-source formula (quoted verbatim from Compute()): iotValue=5; bnsnValue=Math.ceil(0.0010*salesValue)*2 -> IOT=5, BNSN=4; official_value recorded = BNSN (contract-note comparable; the IOT $5 is a separate document duty)
- **IOT 口径对照**：官方 Instrument of Transfer=5 ↔ 本引擎 other_transfer 单文书（$5，stock_other_transfer_doc）=5 → ✅ 一致（口径映射说明见本节开头）
- 截图：[screenshots/07_stock_sale_input.png](screenshots/07_stock_sale_input.png)
- 截图：[screenshots/07_stock_sale_result.png](screenshots/07_stock_sale_result.png)

### 4.2 案例②（自愿/非买卖路径）：value 1,000.01 → 官方 oIOT=5＋0.2% 一次 ceil1（任务期望 8）

- **URL**：https://www.ird.gov.hk/eng/ese/sd_comp/sdshare.htm
- **时间戳**：2026-10-13T20:42:08+08:00 → 2026-10-13T20:42:10+08:00（success）
- **输入步骤**：

1. goto https://www.ird.gov.hk/eng/ese/sd_comp/sdshare.htm (calculator linked from the GovHK eTax share-transfer entry page)
2. select 'Transfer of share by method other than sale or purchase', fill Value of share = 1000.01
3. click official Compute button (input[value=Compute], onclick=Compute())

- **官方显示值原文**：`Current Stamp Duty Payable (Transfer by method other than sale or purchase) input[name=oIOT] displayed value: 8`
- **本引擎值**（app.engines.stamps.stock.calculate_stock_stamp_duty，facts 形状同 tests/engines/stamps/test_stock.py）：status=complete，total=8，detail=documents=[{'doc_kind': 'voluntary_inter_vivos', 'duty': '8'}]
- **对照**（官方值口径见各行观察）：✅ 一致
- 观察：official page-source formula (quoted verbatim from Compute()): iotValue = 5 + Math.ceil(0.0020*othersValue) -> oIOT=8
- 截图：[screenshots/08_stock_voluntary_input.png](screenshots/08_stock_voluntary_input.png)
- 截图：[screenshots/08_stock_voluntary_result.png](screenshots/08_stock_voluntary_result.png)

## 5. 官方输出 × 本引擎 对照汇总表

| 案例 | 官方页面显示值 | 读取方式 | 本引擎值 | 是否一致 | 时间戳（HKT） |
| --- | --- | --- | --- | --- | --- |
| salaries 2024/25（240k/9k/单身） | 2,440 | 官方 eoutdis.htm 结果框架 DOM 文本（_salary_rows 解析 'TAX PAYABLE BY YOU' 各行）＋截图 | 2,440 | ✅ 一致 | 2026-10-13T20:33:27+08:00 → 2026-10-13T20:33:39+08:00 |
| salaries 2025/26（同上） | 940 | 同上 | 940 | ✅ 一致 | 2026-10-13T20:33:39+08:00 → 2026-10-13T20:33:47+08:00 |
| salaries 2026/27（同上） | 3,160 | 同上（该年度无宽减行，官方仅显示单行 'TAX PAYABLE BY YOU'） | 3,160 | ✅ 一致 | 2026-10-13T20:33:47+08:00 → 2026-10-13T20:33:58+08:00 |
| AVD 案例① 2026-06-01 住宅 1 亿 | 4,250,000 | 官方结果只读输入框 input[name=T2] 的显示值 ＋截图 | 4,250,000 | ✅ 一致 | 2026-10-13T20:37:54+08:00 → 2026-10-13T20:37:59+08:00 |
| AVD 案例② 2025-06-01 住宅 4,323,780 | 64,856 | 同上 | 64,856 | ✅ 一致 | 2026-10-13T20:37:59+08:00 → 2026-10-13T20:38:08+08:00 |
| 租约 6 个月总租 60,000 | 150 | 官方结果只读输入框 input[name=T7] 的显示值 ＋截图 | 150 | ✅ 一致 | 2026-10-13T20:38:08+08:00 → 2026-10-13T20:38:14+08:00 |
| 股票买卖 1,000.01：两份 contract note 合计（官方 BNSN） | 4 | 官方结果只读输入框 input[name=BNSN] 的显示值 ＋截图 | 4 | ✅ 一致 | 2026-10-13T20:42:05+08:00 → 2026-10-13T20:42:08+08:00 |
| 股票买卖 1,000.01：转让文书（官方 IOT ↔ 引擎 other_transfer 单文书） | 5 | 同一次结果页 input[name=IOT] 的显示值（引擎侧取 other_transfer 单文书案例） | 5 | ✅ 一致（口径映射见 §4） | 2026-10-13T20:42:05+08:00 → 2026-10-13T20:42:08+08:00 |
| 股票自愿转让 value 1,000.01（官方 oIOT） | 8 | 官方结果只读输入框 input[name=oIOT] 的显示值 ＋截图 | 8 | ✅ 一致 | 2026-10-13T20:42:08+08:00 → 2026-10-13T20:42:10+08:00 |
| 暂缴（下年度暂缴）2024/25 输入（240k/9k） | （官方结果页无此分项，不虚构） | 官方结果页无暂缴分项（见 §6） | 3,940 | 受限（间接核对见下表） | 2026-10-13T20:33:27+08:00 → 2026-10-13T20:33:39+08:00 |
| 暂缴（下年度暂缴）2025/26 输入（240k/9k） | （官方结果页无此分项，不虚构） | 官方结果页无暂缴分项（见 §6） | 3,160 | 受限（间接核对见下表） | 2026-10-13T20:33:39+08:00 → 2026-10-13T20:33:47+08:00 |
| 暂缴（下年度暂缴）2026/27 输入（240k/9k） | （官方结果页无此分项，不虚构） | 官方结果页无暂缴分项（见 §6） | 3,160 | 受限（间接核对见下表） | 2026-10-13T20:33:47+08:00 → 2026-10-13T20:33:58+08:00 |

三年度薪俸税官方页显示的宽减差异（同一输入）：2024/25 宽减 1,500、2025/26 宽减 3,000、2026/27 无宽减条目（且 2026/27 基本免税额改为 145,000，官方页面同输入的累进税额相应为 3,160）——与本引擎 reduction=1500/3000/0 的规则束一致。明细：

| 年度 | 口径 | 官方显示 | 本引擎 | 一致 |
| --- | --- | --- | --- | --- |
| 2024/25 | before_reduction | 3,940 | 3,940 | ✅ 一致 |
| 2024/25 | reduction | 1,500 | 1,500 | ✅ 一致 |
| 2024/25 | final_after_reduction | 2,440 | 2,440 | ✅ 一致 |
| 2025/26 | before_reduction | 3,940 | 3,940 | ✅ 一致 |
| 2025/26 | reduction | 3,000 | 3,000 | ✅ 一致 |
| 2025/26 | final_after_reduction | 940 | 940 | ✅ 一致 |
| 2026/27 | before_reduction | 3,160 | 3,160 | ✅ 一致 |
| 2026/27 | reduction | 0 | 0 | ✅ 一致 |
| 2026/27 | final_after_reduction | 3,160 | 3,160 | ✅ 一致 |

### 5.1 下年度暂缴（next_provisional）：官方无分项 → 与官方下一年度页面的间接核对

引擎口径：next_provisional＝同一事实按下一年度规则估算（下一年度不在规则束内时按引擎现状回退本年度规则），不计宽减（附表 43 宽减不适用暂缴）。官方无暂缴分项，故下表为**间接**核对——用官方『下一年度』计算器页面同输入的税额（本报告 §1 已实时驱动并截图）对照本引擎 next_provisional；每行均如实标注口径，不把间接核对冒充直接对照。

| 输入年度（240k/9k/单身） | 引擎 next_provisional | 引擎规则口径 | 间接官方核对源（同输入） | 间接结论 |
| --- | --- | --- | --- | --- |
| 2024/25 | 3,940 | 2025/26 规则（2025_26 在束内） | 官方 2025/26 页 before_reduction＝3,940 | ✅ 一致（同输入下一年度页面的宽减前税额——宽减不适用暂缴，口径吻合） |
| 2025/26 | 3,160 | 2026/27 规则（2026_27 在束内） | 官方 2026/27 页 TAX PAYABLE＝3,160 | ✅ 一致（同输入下一年度页面税额（该年无宽减条目）） |
| 2026/27 | 3,160 | 2027_28 不在规则束内 → 按引擎现状回退 2026/27 规则（如实说明，非 2027/28 官方规则） | 官方 2026/27 页 TAX PAYABLE（同规则同输入）＝3,160 | ✅ 一致（仅同规则参考值；官方 2027/28 计算器尚未发布，无真正对应页） |

## 6. 方法与限制（含失败尝试，全部如实记录）

### 6.1 失败尝试（本机网络）

| 尝试 | 结果 |
| --- | --- |
| 直接 HTTPS（python urllib / curl） | TLS 握手超时：本机 TUN 代理为 fake-ip DNS，www.ird.gov.hk / www.gov.hk 被解析到 198.18.4.x 假地址；任何 SNI 含 *.gov.hk 的 TLS 连接都停在握手阶段 |
| 经本地混合代理 127.0.0.1:17890（HTTP CONNECT） | CONNECT 隧道建立（HTTP/1.1 200）后上游 TLS 超时；对照 google/github/baidu 经同一代理均 200，说明是代理规则对 *.gov.hk 的路径故障，而非代理整体不可用 |
| 换公共 DNS（8.8.8.8 / 223.5.5.5 UDP:53） | 全部返回同一 fake-ip 198.18.4.x；ipconfig /flushdns 后无变化 |
| webfetch 工具（服务端抓取） | 返回证书校验错误/超时，亦不可用 |
| 真实 IP + SNI（curl --resolve www.ird.gov.hk:443:43.159.109.162） | 仍超时：TUN 按嗅探到的 SNI 域名路由，重新落入故障规则 |
| IPv6 直连真实 AAAA（240d:c010::/32，腾讯 EdgeOne） | 立即失败（本机 IPv6 出口不通/被 TUN 接管） |
| 明文 HTTP 直连真实 IP（Host 头正常） | 仅返回 301 → https，无内容，不可单独使用 |
| 2026-10-13 20:15 瞬时传输故障（本次扩展工作期间的端到端重跑） | 三个薪俸案例在路由抓取层快速失败（每例约 6 秒，20:15:40-51；当时 error 截图已事后删除以免与成功运行产物混淆），入口页驱动随之挂起直至终止该次运行；数分钟后传输路径自行恢复，同代码重跑全部案例成功（无代码变更），判断为边缘节点/代理链路的瞬时故障 |

### 6.2 最终可行的传输变通（如实声明）

可行路径（本脚本实现）：先经本地代理调用 networkcalc.com 的 DNS API 拿到官方站点真实边缘 IP（腾讯 EdgeOne，本次 43.159.109.162；每次运行时实时解析，失败才回退到本次已核验的记录值），再以『无 SNI 的 TLS + 原始 Host 头』直连该 IP 抓取官方页面（curl 验证 200/官方字节数一致；Python 实现见 transport.NoSniHTTPSConnection：wrap_socket(server_hostname=None) 即不发送 SNI）。Playwright 侧用 context.route('**/*') 拦截浏览器请求，以实时抓回的官方字节 fulfill——浏览器仍以官方 origin 加载并执行原始 HTML/CSS/JS，仅传输段被等效替代；页面内容不落盘、不缓存、无任何改动。

影响评估：页面字节与官方服务器逐字节一致（同一次 GET 的响应），浏览器按官方 origin 加载并执行；唯一被替代的是本机到官方服务器之间的传输路径。无 SNI 仅是 TLS 客户端行为，服务器侧（EdgeOne）按 Host 头正常服务，返回内容与浏览器直连完全相同。

### 6.3 页面级变通

stc.htm（薪俸税）为旧式 frameset 设计：initPage()→restorefrmIn() 依赖 frameset 提供的 parent.frmInStatus，DisplayResult() 跳转 parent.main.location.href=“eoutdis.htm”，单开时两者皆缺。处理方式＝最小 parent shim（任务预授权）：①先调用官方自身的 savefrmIn() 把『未填写的初始表单』快照进 parent.frmInStatus，再刷新页面让官方 initPage() 正常跑完（含 GetDeduction() 设定年度全局量）；②补一个空 <iframe name="main"> 作为官方结果页 eoutdis.htm 的落点。计算与结果渲染全部为官方原始代码，未修改任何页面逻辑；结果从该框架内官方渲染的可见文本读取。

### 6.4 官方计算器口径限制

GovHK 印花税入口页本身不含计算表单，页面内链接指向 IRD sd_comp 计算器；本次按入口页链接进入 sdpt_s2_2026.htm（住宅 AVD，Scale 1 Part 1 ＋ Scale 2）。该官方计算器无『文书日期』输入，按其页面说明只计算现行印花税；2026-02-26 起的现行住宅档位在 ≤21,739,120 区间与 2025-02-26 起的 Table 9 完全同档，故案例②（2025-06-01，4,323,780）官方现行计算器的输出与 2025 制度下的期望值可比。该口径限制如实记录。

官方租约计算器（csdta.htm，§1.14 台账 URL）自身约定：租期 ≤1 年必须输入『全期总租金』，月租输入会被页面弹窗拒绝（本次实测留证）。故官方驱动采用等价输入：总租金 60,000（＝月租 10,000 × 6 个月），租期 2025-07-01..2025-12-31；本引擎侧 facts 形状按 tests/engines/stamps/test_lease.py 的 rent_input_mode='total_rent' 同口径。

### 6.5 暂缴分项核验（新增对比项 A）

暂缴分项核验记录（2026-10-13，官方字节逐字核验）：IRD 薪俸税计算器三个年度（st_comp_2024_25／2025_26／2026_27）的 stc.htm 与结果页模板 eoutdis.htm 源码中『provisional』字样出现 0 次——结果页（官方 eoutdis.htm 渲染）仅含本年度 TOTAL INCOME／DEDUCTIONS／ALLOWANCES／NET CHARGEABLE INCOME／TAX PAYABLE BY YOU 各行与 Disclaimer，不含『Provisional tax for the following year』分项；该计算器亦无暂缴相关输入。中文版路径（/chi/ese/st_comp_*/）返回 404（计算器仅英文）。因此『官方显示的下年度暂缴』无从取得，本次如实记录为『官方无该分项』，不虚构数值；驱动器每年度仍实时解析结果文本中的 provisional 行（有则记录原文，无则如实留痕）。本引擎 next_provisional 与官方数值的间接核对见 §5。

### 6.6 股票计算器调研与口径差异（新增对比项 B）

股票计算器调研留痕（2026-10-13，全部经本目录 transport 实时访问官方域名或公开检索）：①GovHK 印花税入口页 stamp_duty_computation.htm 链接枚举：仅住宅 AVD（sdpt_s2_2026.htm）、非住宅 AVD（sdpt_s3_2026.htm）、租约（sdta.htm）三个计算器链接，无股票；②公开检索（官方域名限定）命中 IRD 官方『Stamp Duty Computation - Share Transfer』（ird.gov.hk/eng/ese/sd_comp/sdshare.htm，Last revision date: 17 November 2023）与 GovHK eTax 入口页『Stamp Duty Computation (Share Transfer Document)』（gov.hk/.../share_transfer_computation.htm，页面内 'Share Transfer Document' 链接指向 sdshare.htm）；③两页本次运行均实时 200 抓取核验。**任务简报预期『官方股票计算器不存在』与实况不符，已据实修正：官方计算器公开存在，本次按『若存在 → 同法实时对比』分支执行，未使用任何第三方计算器。**

口径差异（如实记录，非不一致）：官方股票计算器按『交易视图』显示一次买卖的印花税＝ BNSN（两份 contract note 合计，页面源码公式 bnsnValue=Math.ceil(0.0010×consideration)×2） ＋ IOT（转让文书固定 $5，恒显示）。本引擎按 D2.2 红线逐份文书计税：contract note 各 0.1% 分别 ceil1、sold/bought 不合并；**不得**在无 other_transfer 文书事实时自动附加转让文书 $5。故对照按两组口径映射列示：『官方 BNSN ↔ 引擎两 note 合计』、『官方 IOT ↔ 引擎 other_transfer 单文书（$5）』；自愿/非买卖路径官方 oIOT=5+Math.ceil(0.0020×value) 与引擎 voluntary_inter_vivos（$5＋0.2% of value，一次 ceil1）同口径直比。

### 6.7 本次运行的传输日志（逐请求）

```text
resolve(www.ird.gov.hk) -> 43.159.109.162
GET https://www.ird.gov.hk/eng/ese/st_comp_2024_25/stc.htm -> 200 (124429 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2024_25/jquery.js -> 200 (29846 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2024_25/eoutdis.htm -> 200 (83139 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2024_25/jquery.js -> 200 (29846 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2025_26/stc.htm -> 200 (128603 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2025_26/jquery.js -> 200 (29846 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2025_26/eoutdis.htm -> 200 (82915 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2025_26/jquery.js -> 200 (29846 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2026_27/stc.htm -> 200 (128681 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2026_27/jquery.js -> 200 (29846 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2026_27/eoutdis.htm -> 200 (83035 bytes)
GET https://www.ird.gov.hk/eng/ese/st_comp_2026_27/jquery.js -> 200 (29846 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/images/copy.gif -> 200 (86 bytes)
resolve(www.gov.hk) -> 43.159.109.162
GET https://www.gov.hk/en/residents/taxes/etax/services/stamp_duty_computation.htm -> 200 (89242 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15.css?20210216.114829%22 -> 200 (4437 bytes)
GET https://www.gov.hk/css/reset.min.css?20170105.144318 -> 200 (2210 bytes)
GET https://www.gov.hk/css/owl.carousel.css?20170728.150737 -> 200 (4526 bytes)
GET https://www.gov.hk/css/lightslider.css?20170509.141207 -> 200 (8431 bytes)
GET https://www.gov.hk/css/bootstrap.css?20170105.144316 -> 200 (24099 bytes)
GET https://www.gov.hk/css/jquery.cover.css?20170105.144338 -> 200 (765 bytes)
GET https://www.gov.hk/css/layout.css?20260226.161100 -> 200 (455262 bytes)
GET https://www.gov.hk/css/layout_icon.css?20200123.092830 -> 200 (81710 bytes)
GET https://www.gov.hk/css/layout_append.css?20210607.145803 -> 200 (45561 bytes)
GET https://www.gov.hk/applications/user_exp/feedback.css?20210430.101915 -> 200 (9286 bytes)
GET https://www.gov.hk/applications/uss_bd/uss_bd.css?20200904.101013 -> 200 (14933 bytes)
GET https://www.gov.hk/applications/survey_banner/health/survey_banner.css?20240430.095018 -> 200 (844 bytes)
GET https://www.gov.hk/css/typography.css?20230803.155625 -> 200 (49718 bytes)
GET https://www.gov.hk/css/print.css?20210113.093438 -> 200 (19804 bytes)
GET https://www.gov.hk/css/swiper.css?20170105.144318 -> 200 (20672 bytes)
GET https://www.gov.hk/css/swiper-slider.css?20170512.092140 -> 200 (5314 bytes)
GET https://www.gov.hk/applications/chatbot/chatbot_iframe.css?20230615.143302 -> 200 (2655 bytes)
GET https://www.gov.hk/css/content.min.css?20260813.180000 -> 200 (34434 bytes)
GET https://www.gov.hk/js/libs/jquery-3.7.1.min.js?20240326.152545 -> 200 (87533 bytes)
GET https://www.gov.hk/js/libs/jquery-ui-1.14.1.js?20250827.173537 -> 200 (521054 bytes)
GET https://www.gov.hk/js/libs/jquery.cookie.js?20161109.151942 -> 200 (3252 bytes)
GET https://www.gov.hk/js/prepost/init.js?20210216.114831 -> 200 (54 bytes)
GET https://www.gov.hk/js/prepost/webstat_header_js.htm?20231017.115953 -> 200 (285 bytes)
GET https://www.gov.hk/js/prepost/domain_js.htm?20260109.133801 -> 200 (169 bytes)
GET https://www.gov.hk/js/feedin.js?20261007.162006 -> 200 (170745 bytes)
GET https://www.gov.hk/js/search_portal.js?20210216.114831 -> 200 (6175 bytes)
GET https://www.gov.hk/js/libs/jquery.platformselector.js?20161109.151942 -> 200 (4302 bytes)
GET https://www.gov.hk/js/libs/jquery.cover.js?20161109.151942 -> 200 (6386 bytes)
GET https://www.gov.hk/js/libs/jquery.easing.1.3.js?20161109.151942 -> 200 (8301 bytes)
GET https://www.gov.hk/js/libs/masonry.pkgd.min.js?20161109.151942 -> 200 (90287 bytes)
GET https://www.gov.hk/js/libs/owl.carousel.js?20161109.151942 -> 200 (85172 bytes)
GET https://www.gov.hk/js/libs/lightslider.js?20161109.151942 -> 200 (49218 bytes)
GET https://www.gov.hk/js/libs/grayscale.js?20161109.151941 -> 200 (13215 bytes)
GET https://www.gov.hk/js/anchor.js?20241022.1801 -> 200 (25 bytes)
GET https://www.gov.hk/js/govhk.js?20261007.162006 -> 200 (69004 bytes)
GET https://www.gov.hk/js/top.js?20210216.114831 -> 200 (8569 bytes)
GET https://www.gov.hk/js/libs/swiper.js?20200814.103914 -> 200 (234794 bytes)
GET https://www.gov.hk/js/swiper-slider.js?20180214.101056 -> 200 (9746 bytes)
GET https://www.gov.hk/js/libs/underscore-min.js?20241107.161521 -> 200 (19571 bytes)
GET https://www.gov.hk/js/layout_culture.js?20260625.104800 -> 200 (19465 bytes)
GET https://www.gov.hk/js/share_with_wechat.js?20210216.114831 -> 200 (2622 bytes)
GET https://www.gov.hk/js/qrcode.js?20191031.114200 -> 200 (33782 bytes)
GET https://www.gov.hk/js/jquery.qrcode.min.js?20191031.114200 -> 200 (14023 bytes)
GET https://www.gov.hk/js/prepost/post.js?20210216.114831 -> 200 (66 bytes)
GET https://www.gov.hk/en/images/lower_promo/Baby3_550_EN.jpg -> 200 (36359 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2026/budget.gif -> 200 (13784 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2026/er_efiling_iamsmart_en.jpg -> 200 (54220 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2019/stamp_banner.gif -> 200 (35113 bytes)
GET https://www.gov.hk/images/feedback/good.png -> 200 (573 bytes)
GET https://www.gov.hk/images/feedback/ok.png -> 200 (468 bytes)
GET https://www.gov.hk/images/feedback/bad.png -> 200 (589 bytes)
GET https://www.gov.hk/images/footer/wcag2AA.gif -> 200 (1421 bytes)
GET https://www.gov.hk/images/footer/ipv6.jpg -> 200 (4153 bytes)
GET https://www.gov.hk/images/footer/brandhk.gif -> 200 (3997 bytes)
GET https://www.gov.hk/applications/user_exp/polyfill.min.js?20200616.100033 -> 200 (3136 bytes)
GET https://www.gov.hk/images/logo_main.svg -> 200 (24983 bytes)
GET https://www.gov.hk/images/common_icons/orange/icon_search_orange.svg -> 200 (1623 bytes)
GET https://www.gov.hk/images/common_icons/grey/icon_arrow_left_grey.svg -> 200 (639 bytes)
GET https://www.gov.hk/images/common_icons/grey/icon_arrow_right_grey.svg -> 200 (630 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_facebook.svg -> 200 (1024 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_twitter.svg -> 200 (1412 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_weibo.svg -> 200 (3060 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_whatsapp.svg -> 200 (2849 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_line.svg -> 200 (2386 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_wechat.svg -> 200 (2695 bytes)
GET https://www.gov.hk/images/common_icons/white/icon_arrow_right_white.svg -> 200 (630 bytes)
GET https://www.gov.hk/images/common_icons/bw/icon_arrow_right_bw.svg -> 200 (630 bytes)
GET https://www.gov.hk/images/common_icons/grey/icon_arrow2_up_grey.svg -> 200 (635 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15-vietnamese_latin-ext_latin_cyrillic-ext_cyrillic-regular.woff2 -> 200 (52488 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15-vietnamese_latin-ext_latin_cyrillic-ext_cyrillic-700.woff2 -> 200 (52488 bytes)
GET https://www.gov.hk/applications/user_exp/fetch.umd.js?20200616.100033 -> 200 (14805 bytes)
GET https://www.gov.hk/applications/user_exp/user_exp_common.js?20210216.114828 -> 200 (1651 bytes)
GET https://www.gov.hk/en/residents/taxes/etax/services/stamp_duty_computation.htm -> 200 (89242 bytes)
GET https://www.gov.hk/applications/user_exp/feedback.js?20210430.101915 -> 200 (6485 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15.css?20210216.114829%22 -> 200 (4437 bytes)
GET https://www.gov.hk/css/reset.min.css?20170105.144318 -> 200 (2210 bytes)
GET https://www.gov.hk/css/owl.carousel.css?20170728.150737 -> 200 (4526 bytes)
GET https://www.gov.hk/css/lightslider.css?20170509.141207 -> 200 (8431 bytes)
GET https://www.gov.hk/css/bootstrap.css?20170105.144316 -> 200 (24099 bytes)
GET https://www.gov.hk/css/jquery.cover.css?20170105.144338 -> 200 (765 bytes)
GET https://www.gov.hk/css/layout.css?20260226.161100 -> 200 (455262 bytes)
GET https://www.gov.hk/css/layout_icon.css?20200123.092830 -> 200 (81710 bytes)
GET https://www.gov.hk/css/layout_append.css?20210607.145803 -> 200 (45561 bytes)
GET https://www.gov.hk/applications/user_exp/feedback.css?20210430.101915 -> 200 (9286 bytes)
GET https://www.gov.hk/applications/uss_bd/uss_bd.css?20200904.101013 -> 200 (14933 bytes)
GET https://www.gov.hk/applications/survey_banner/health/survey_banner.css?20240430.095018 -> 200 (844 bytes)
GET https://www.gov.hk/css/typography.css?20230803.155625 -> 200 (49718 bytes)
GET https://www.gov.hk/css/print.css?20210113.093438 -> 200 (19804 bytes)
GET https://www.gov.hk/css/swiper.css?20170105.144318 -> 200 (20672 bytes)
GET https://www.gov.hk/css/swiper-slider.css?20170512.092140 -> 200 (5314 bytes)
GET https://www.gov.hk/applications/chatbot/chatbot_iframe.css?20230615.143302 -> 200 (2655 bytes)
GET https://www.gov.hk/css/content.min.css?20260813.180000 -> 200 (34434 bytes)
GET https://www.gov.hk/js/libs/jquery-3.7.1.min.js?20240326.152545 -> 200 (87533 bytes)
GET https://www.gov.hk/js/libs/jquery-ui-1.14.1.js?20250827.173537 -> 200 (521054 bytes)
GET https://www.gov.hk/js/libs/jquery.cookie.js?20161109.151942 -> 200 (3252 bytes)
GET https://www.gov.hk/js/prepost/init.js?20210216.114831 -> 200 (54 bytes)
GET https://www.gov.hk/js/prepost/webstat_header_js.htm?20231017.115953 -> 200 (285 bytes)
GET https://www.gov.hk/js/prepost/domain_js.htm?20260109.133801 -> 200 (169 bytes)
GET https://www.gov.hk/js/feedin.js?20261007.162006 -> 200 (170745 bytes)
GET https://www.gov.hk/js/search_portal.js?20210216.114831 -> 200 (6175 bytes)
GET https://www.gov.hk/js/libs/jquery.platformselector.js?20161109.151942 -> 200 (4302 bytes)
GET https://www.gov.hk/js/libs/jquery.cover.js?20161109.151942 -> 200 (6386 bytes)
GET https://www.gov.hk/js/libs/jquery.easing.1.3.js?20161109.151942 -> 200 (8301 bytes)
GET https://www.gov.hk/js/libs/masonry.pkgd.min.js?20161109.151942 -> 200 (90287 bytes)
GET https://www.gov.hk/js/libs/owl.carousel.js?20161109.151942 -> 200 (85172 bytes)
GET https://www.gov.hk/js/libs/lightslider.js?20161109.151942 -> 200 (49218 bytes)
GET https://www.gov.hk/js/libs/grayscale.js?20161109.151941 -> 200 (13215 bytes)
GET https://www.gov.hk/js/anchor.js?20241022.1801 -> 200 (25 bytes)
GET https://www.gov.hk/js/govhk.js?20261007.162006 -> 200 (69004 bytes)
GET https://www.gov.hk/js/top.js?20210216.114831 -> 200 (8569 bytes)
GET https://www.gov.hk/js/libs/swiper.js?20200814.103914 -> 200 (234794 bytes)
GET https://www.gov.hk/js/swiper-slider.js?20180214.101056 -> 200 (9746 bytes)
GET https://www.gov.hk/js/libs/underscore-min.js?20241107.161521 -> 200 (19571 bytes)
GET https://www.gov.hk/js/layout_culture.js?20260625.104800 -> 200 (19465 bytes)
GET https://www.gov.hk/js/share_with_wechat.js?20210216.114831 -> 200 (2622 bytes)
GET https://www.gov.hk/js/qrcode.js?20191031.114200 -> 200 (33782 bytes)
GET https://www.gov.hk/js/jquery.qrcode.min.js?20191031.114200 -> 200 (14023 bytes)
GET https://www.gov.hk/js/prepost/post.js?20210216.114831 -> 200 (66 bytes)
GET https://www.gov.hk/en/images/lower_promo/Baby3_550_EN.jpg -> 200 (36359 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2026/budget.gif -> 200 (13784 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2026/er_efiling_iamsmart_en.jpg -> 200 (54220 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2019/stamp_banner.gif -> 200 (35113 bytes)
GET https://www.gov.hk/images/feedback/good.png -> 200 (573 bytes)
GET https://www.gov.hk/images/feedback/ok.png -> 200 (468 bytes)
GET https://www.gov.hk/images/feedback/bad.png -> 200 (589 bytes)
GET https://www.gov.hk/images/footer/wcag2AA.gif -> 200 (1421 bytes)
GET https://www.gov.hk/images/footer/ipv6.jpg -> 200 (4153 bytes)
GET https://www.gov.hk/images/footer/brandhk.gif -> 200 (3997 bytes)
GET https://www.gov.hk/applications/user_exp/polyfill.min.js?20200616.100033 -> 200 (3136 bytes)
GET https://www.gov.hk/images/logo_main.svg -> 200 (24983 bytes)
GET https://www.gov.hk/images/common_icons/orange/icon_search_orange.svg -> 200 (1623 bytes)
GET https://www.gov.hk/images/common_icons/grey/icon_arrow_left_grey.svg -> 200 (639 bytes)
GET https://www.gov.hk/images/common_icons/grey/icon_arrow_right_grey.svg -> 200 (630 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_facebook.svg -> 200 (1024 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_twitter.svg -> 200 (1412 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_weibo.svg -> 200 (3060 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_whatsapp.svg -> 200 (2849 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_line.svg -> 200 (2386 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_wechat.svg -> 200 (2695 bytes)
GET https://www.gov.hk/images/common_icons/white/icon_arrow_right_white.svg -> 200 (630 bytes)
GET https://www.gov.hk/images/common_icons/bw/icon_arrow_right_bw.svg -> 200 (630 bytes)
GET https://www.gov.hk/images/common_icons/grey/icon_arrow2_up_grey.svg -> 200 (635 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15-vietnamese_latin-ext_latin_cyrillic-ext_cyrillic-regular.woff2 -> 200 (52488 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15-vietnamese_latin-ext_latin_cyrillic-ext_cyrillic-700.woff2 -> 200 (52488 bytes)
GET https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm -> 200 (9245 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/common/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm -> 200 (9245 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/common/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/chi/ese/sd_comp/csdta.htm -> 200 (25802 bytes)
GET https://www.ird.gov.hk/chi/ese/image/banner.gif -> 200 (5075 bytes)
GET https://www.ird.gov.hk/images/copy.gif -> 200 (86 bytes)
GET https://www.gov.hk/en/residents/taxes/etax/services/share_transfer_computation.htm -> 200 (88635 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15.css?20210216.114829%22 -> 200 (4437 bytes)
GET https://www.gov.hk/css/reset.min.css?20170105.144318 -> 200 (2210 bytes)
GET https://www.gov.hk/css/owl.carousel.css?20170728.150737 -> 200 (4526 bytes)
GET https://www.gov.hk/css/lightslider.css?20170509.141207 -> 200 (8431 bytes)
GET https://www.gov.hk/css/bootstrap.css?20170105.144316 -> 200 (24099 bytes)
GET https://www.gov.hk/css/jquery.cover.css?20170105.144338 -> 200 (765 bytes)
GET https://www.gov.hk/css/layout.css?20260226.161100 -> 200 (455262 bytes)
GET https://www.gov.hk/css/layout_icon.css?20200123.092830 -> 200 (81710 bytes)
GET https://www.gov.hk/css/layout_append.css?20210607.145803 -> 200 (45561 bytes)
GET https://www.gov.hk/applications/user_exp/feedback.css?20210430.101915 -> 200 (9286 bytes)
GET https://www.gov.hk/applications/uss_bd/uss_bd.css?20200904.101013 -> 200 (14933 bytes)
GET https://www.gov.hk/applications/survey_banner/health/survey_banner.css?20240430.095018 -> 200 (844 bytes)
GET https://www.gov.hk/css/typography.css?20230803.155625 -> 200 (49718 bytes)
GET https://www.gov.hk/css/print.css?20210113.093438 -> 200 (19804 bytes)
GET https://www.gov.hk/css/swiper.css?20170105.144318 -> 200 (20672 bytes)
GET https://www.gov.hk/css/swiper-slider.css?20170512.092140 -> 200 (5314 bytes)
GET https://www.gov.hk/applications/chatbot/chatbot_iframe.css?20230615.143302 -> 200 (2655 bytes)
GET https://www.gov.hk/css/content.min.css?20260813.180000 -> 200 (34434 bytes)
GET https://www.gov.hk/js/libs/jquery-3.7.1.min.js?20240326.152545 -> 200 (87533 bytes)
GET https://www.gov.hk/js/libs/jquery-ui-1.14.1.js?20250827.173537 -> 200 (521054 bytes)
GET https://www.gov.hk/js/libs/jquery.cookie.js?20161109.151942 -> 200 (3252 bytes)
GET https://www.gov.hk/js/prepost/init.js?20210216.114831 -> 200 (54 bytes)
GET https://www.gov.hk/js/prepost/webstat_header_js.htm?20231017.115953 -> 200 (285 bytes)
GET https://www.gov.hk/js/prepost/domain_js.htm?20260109.133801 -> 200 (169 bytes)
GET https://www.gov.hk/js/feedin.js?20261007.162006 -> 200 (170745 bytes)
GET https://www.gov.hk/js/search_portal.js?20210216.114831 -> 200 (6175 bytes)
GET https://www.gov.hk/js/libs/jquery.platformselector.js?20161109.151942 -> 200 (4302 bytes)
GET https://www.gov.hk/js/libs/jquery.cover.js?20161109.151942 -> 200 (6386 bytes)
GET https://www.gov.hk/js/libs/jquery.easing.1.3.js?20161109.151942 -> 200 (8301 bytes)
GET https://www.gov.hk/js/libs/masonry.pkgd.min.js?20161109.151942 -> 200 (90287 bytes)
GET https://www.gov.hk/js/libs/owl.carousel.js?20161109.151942 -> 200 (85172 bytes)
GET https://www.gov.hk/js/libs/lightslider.js?20161109.151942 -> 200 (49218 bytes)
GET https://www.gov.hk/js/libs/grayscale.js?20161109.151941 -> 200 (13215 bytes)
GET https://www.gov.hk/js/anchor.js?20241022.1801 -> 200 (25 bytes)
GET https://www.gov.hk/js/govhk.js?20261007.162006 -> 200 (69004 bytes)
GET https://www.gov.hk/js/top.js?20210216.114831 -> 200 (8569 bytes)
GET https://www.gov.hk/js/libs/swiper.js?20200814.103914 -> 200 (234794 bytes)
GET https://www.gov.hk/js/swiper-slider.js?20180214.101056 -> 200 (9746 bytes)
GET https://www.gov.hk/js/libs/underscore-min.js?20241107.161521 -> 200 (19571 bytes)
GET https://www.gov.hk/js/layout_culture.js?20260625.104800 -> 200 (19465 bytes)
GET https://www.gov.hk/js/share_with_wechat.js?20210216.114831 -> 200 (2622 bytes)
GET https://www.gov.hk/js/qrcode.js?20191031.114200 -> 200 (33782 bytes)
GET https://www.gov.hk/js/jquery.qrcode.min.js?20191031.114200 -> 200 (14023 bytes)
GET https://www.gov.hk/js/prepost/post.js?20210216.114831 -> 200 (66 bytes)
GET https://www.gov.hk/en/images/lower_promo/Baby3_550_EN.jpg -> 200 (36359 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2026/budget.gif -> 200 (13784 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2026/er_efiling_iamsmart_en.jpg -> 200 (54220 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2019/stamp_banner.gif -> 200 (35113 bytes)
GET https://www.gov.hk/images/feedback/good.png -> 200 (573 bytes)
GET https://www.gov.hk/images/feedback/ok.png -> 200 (468 bytes)
GET https://www.gov.hk/images/feedback/bad.png -> 200 (589 bytes)
GET https://www.gov.hk/images/footer/wcag2AA.gif -> 200 (1421 bytes)
GET https://www.gov.hk/images/footer/ipv6.jpg -> 200 (4153 bytes)
GET https://www.gov.hk/images/footer/brandhk.gif -> 200 (3997 bytes)
GET https://www.gov.hk/applications/user_exp/polyfill.min.js?20200616.100033 -> 200 (3136 bytes)
GET https://www.gov.hk/images/logo_main.svg -> 200 (24983 bytes)
GET https://www.gov.hk/images/common_icons/orange/icon_search_orange.svg -> 200 (1623 bytes)
GET https://www.gov.hk/images/common_icons/grey/icon_arrow_left_grey.svg -> 200 (639 bytes)
GET https://www.gov.hk/images/common_icons/grey/icon_arrow_right_grey.svg -> 200 (630 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_facebook.svg -> 200 (1024 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_twitter.svg -> 200 (1412 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_weibo.svg -> 200 (3060 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_whatsapp.svg -> 200 (2849 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_line.svg -> 200 (2386 bytes)
GET https://www.gov.hk/images/common_icons/icon_share_wechat.svg -> 200 (2695 bytes)
GET https://www.gov.hk/images/common_icons/bw/icon_arrow_right_bw.svg -> 200 (630 bytes)
GET https://www.gov.hk/images/common_icons/grey/icon_arrow2_up_grey.svg -> 200 (635 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15-vietnamese_latin-ext_latin_cyrillic-ext_cyrillic-regular.woff2 -> 200 (52488 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15-vietnamese_latin-ext_latin_cyrillic-ext_cyrillic-700.woff2 -> 200 (52488 bytes)
GET https://www.gov.hk/applications/user_exp/fetch.umd.js?20200616.100033 -> 200 (14805 bytes)
GET https://www.gov.hk/en/residents/taxes/etax/services/share_transfer_computation.htm -> 200 (88635 bytes)
GET https://www.gov.hk/applications/user_exp/user_exp_common.js?20210216.114828 -> 200 (1651 bytes)
GET https://www.gov.hk/css/fonts/montserrat/montserrat-v15.css?20210216.114829%22 -> 200 (4437 bytes)
GET https://www.gov.hk/css/reset.min.css?20170105.144318 -> 200 (2210 bytes)
GET https://www.gov.hk/css/owl.carousel.css?20170728.150737 -> 200 (4526 bytes)
GET https://www.gov.hk/css/lightslider.css?20170509.141207 -> 200 (8431 bytes)
GET https://www.gov.hk/css/bootstrap.css?20170105.144316 -> 200 (24099 bytes)
GET https://www.gov.hk/css/jquery.cover.css?20170105.144338 -> 200 (765 bytes)
GET https://www.gov.hk/css/layout.css?20260226.161100 -> 200 (455262 bytes)
GET https://www.gov.hk/css/layout_icon.css?20200123.092830 -> 200 (81710 bytes)
GET https://www.gov.hk/css/layout_append.css?20210607.145803 -> 200 (45561 bytes)
GET https://www.gov.hk/applications/user_exp/feedback.css?20210430.101915 -> 200 (9286 bytes)
GET https://www.gov.hk/applications/uss_bd/uss_bd.css?20200904.101013 -> 200 (14933 bytes)
GET https://www.gov.hk/applications/survey_banner/health/survey_banner.css?20240430.095018 -> 200 (844 bytes)
GET https://www.gov.hk/css/typography.css?20230803.155625 -> 200 (49718 bytes)
GET https://www.gov.hk/css/print.css?20210113.093438 -> 200 (19804 bytes)
GET https://www.gov.hk/css/swiper.css?20170105.144318 -> 200 (20672 bytes)
GET https://www.gov.hk/css/swiper-slider.css?20170512.092140 -> 200 (5314 bytes)
GET https://www.gov.hk/applications/chatbot/chatbot_iframe.css?20230615.143302 -> 200 (2655 bytes)
GET https://www.gov.hk/css/content.min.css?20260813.180000 -> 200 (34434 bytes)
GET https://www.gov.hk/js/libs/jquery-3.7.1.min.js?20240326.152545 -> 200 (87533 bytes)
GET https://www.gov.hk/js/libs/jquery-ui-1.14.1.js?20250827.173537 -> 200 (521054 bytes)
GET https://www.gov.hk/js/libs/jquery.cookie.js?20161109.151942 -> 200 (3252 bytes)
GET https://www.gov.hk/js/prepost/init.js?20210216.114831 -> 200 (54 bytes)
GET https://www.gov.hk/js/prepost/webstat_header_js.htm?20231017.115953 -> 200 (285 bytes)
GET https://www.gov.hk/js/prepost/domain_js.htm?20260109.133801 -> 200 (169 bytes)
GET https://www.gov.hk/js/feedin.js?20261007.162006 -> 200 (170745 bytes)
GET https://www.gov.hk/js/search_portal.js?20210216.114831 -> 200 (6175 bytes)
GET https://www.gov.hk/js/libs/jquery.platformselector.js?20161109.151942 -> 200 (4302 bytes)
GET https://www.gov.hk/js/libs/jquery.cover.js?20161109.151942 -> 200 (6386 bytes)
GET https://www.gov.hk/js/libs/jquery.easing.1.3.js?20161109.151942 -> 200 (8301 bytes)
GET https://www.gov.hk/js/libs/masonry.pkgd.min.js?20161109.151942 -> 200 (90287 bytes)
GET https://www.gov.hk/js/libs/owl.carousel.js?20161109.151942 -> 200 (85172 bytes)
GET https://www.gov.hk/js/libs/lightslider.js?20161109.151942 -> 200 (49218 bytes)
GET https://www.gov.hk/js/libs/grayscale.js?20161109.151941 -> 200 (13215 bytes)
GET https://www.gov.hk/js/anchor.js?20241022.1801 -> 200 (25 bytes)
GET https://www.gov.hk/js/govhk.js?20261007.162006 -> 200 (69004 bytes)
GET https://www.gov.hk/js/top.js?20210216.114831 -> 200 (8569 bytes)
GET https://www.gov.hk/js/libs/swiper.js?20200814.103914 -> 200 (234794 bytes)
GET https://www.gov.hk/js/swiper-slider.js?20180214.101056 -> 200 (9746 bytes)
GET https://www.gov.hk/js/libs/underscore-min.js?20241107.161521 -> 200 (19571 bytes)
GET https://www.gov.hk/js/layout_culture.js?20260625.104800 -> 200 (19465 bytes)
GET https://www.gov.hk/js/share_with_wechat.js?20210216.114831 -> 200 (2622 bytes)
GET https://www.gov.hk/js/qrcode.js?20191031.114200 -> 200 (33782 bytes)
GET https://www.gov.hk/js/jquery.qrcode.min.js?20191031.114200 -> 200 (14023 bytes)
GET https://www.gov.hk/js/prepost/post.js?20210216.114831 -> 200 (66 bytes)
GET https://www.gov.hk/en/images/lower_promo/Baby3_550_EN.jpg -> 200 (36359 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2026/budget.gif -> 200 (13784 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2026/er_efiling_iamsmart_en.jpg -> 200 (54220 bytes)
GET https://www.gov.hk/en/residents/taxes/images/2019/stamp_banner.gif -> 200 (35113 bytes)
GET https://www.gov.hk/images/feedback/good.png -> 200 (573 bytes)
GET https://www.gov.hk/images/feedback/ok.png -> 200 (468 bytes)
GET https://www.gov.hk/images/feedback/bad.png -> 200 (589 bytes)
GET https://www.gov.hk/images/footer/wcag2AA.gif -> 200 (1421 bytes)
GET https://www.gov.hk/images/footer/ipv6.jpg -> 200 (4153 bytes)
GET https://www.gov.hk/images/footer/brandhk.gif -> 200 (3997 bytes)
GET https://www.gov.hk/applications/user_exp/polyfill.min.js?20200616.100033 -> 200 (3136 bytes)
GET https://www.ird.gov.hk/eng/ese/sd_comp/sdshare.htm -> 200 (9167 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/common/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/eng/ese/sd_comp/sdshare.htm -> 200 (9167 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/common/images/copy.gif -> 200 (86 bytes)
```

## 7. 复现方式

```powershell
# 仓库根目录；端到端重跑（结果应可复现，时间戳与网络抖动除外）
.venv\Scripts\python.exe -m scripts.official_calc_compare.run_all
```

脚本目录：`scripts/official_calc_compare/`（transport.py 传输层、drivers.py 页面驱动（薪俸三年度＋AVD＋租约＋股票 sdshare）、local_engine.py 引擎对照、run_all.py 编排与报告生成）。网络仅访问 www.ird.gov.hk / www.gov.hk（官方域名）与 networkcalc.com（DNS 解析 API）。
