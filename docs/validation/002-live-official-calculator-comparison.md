# 002 - 官方计算器实时驱动对比（Playwright，IRD/GovHK）

任务：用 Playwright 实时驱动 IRD/GovHK 官方网页计算器，与本仓库 app.engines 纯函数对同一输入的输出对照。

- **运行时间**：2026-10-13T09:47:43+08:00 ～ 2026-10-13T09:49:59+08:00（HKT）
- **环境**：Windows 10 + 本仓库 .venv（playwright 1.63 + chromium，无头模式）；网络仅访问官方域名 www.ird.gov.hk / www.gov.hk（外加解析真实 IP 用的 networkcalc.com DNS API）。
- **性质**：尽力而为的验证记录；每项如实标注 成功 / 受限，含全部失败尝试。
- **声明**：仅驱动官方 UI（填字段、点 Compute、读显示值），未修改任何官方页面逻辑；所有页面字节均在运行时从官方服务器实时取回。本目录与 screenshots/ 为本次新增，未改动仓库其他文件。

## 1. 薪俸税计算器（IRD stc.htm，三年度）

输入（三年度相同）：单身、受雇收入 240,000、MPF 强制供款 9,000，其余全部留 0（官方页面默认）。

stc.htm（薪俸税）为旧式 frameset 设计：initPage()→restorefrmIn() 依赖 frameset 提供的 parent.frmInStatus，DisplayResult() 跳转 parent.main.location.href=“eoutdis.htm”，单开时两者皆缺。处理方式＝最小 parent shim（任务预授权）：①先调用官方自身的 savefrmIn() 把『未填写的初始表单』快照进 parent.frmInStatus，再刷新页面让官方 initPage() 正常跑完（含 GetDeduction() 设定年度全局量）；②补一个空 <iframe name="main"> 作为官方结果页 eoutdis.htm 的落点。计算与结果渲染全部为官方原始代码，未修改任何页面逻辑；结果从该框架内官方渲染的可见文本读取。

### 1.1 2024/25（st_comp_2024_25/stc.htm）

- **URL**：https://www.ird.gov.hk/eng/ese/st_comp_2024_25/stc.htm
- **时间戳**：2026-10-13T09:47:43+08:00 → 2026-10-13T09:47:52+08:00（success）
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
- **对照**：✅ 一致（final 口径；before/reduction 同表见 §4）
- 观察：parsed from displayed rows: before_reduction=3940, final_after_reduction=2440
- 观察：no two-tiered standard-rate remark -> progressive-rate path chosen
- 截图：[screenshots/01_stc_2024_25_input.png](screenshots/01_stc_2024_25_input.png)
- 截图：[screenshots/01_stc_2024_25_result.png](screenshots/01_stc_2024_25_result.png)

### 1.2 2025/26（st_comp_2025_26/stc.htm）

- **URL**：https://www.ird.gov.hk/eng/ese/st_comp_2025_26/stc.htm
- **时间戳**：2026-10-13T09:47:52+08:00 → 2026-10-13T09:48:01+08:00（success）
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
- **对照**：✅ 一致（final 口径；before/reduction 同表见 §4）
- 观察：parsed from displayed rows: before_reduction=3940, final_after_reduction=940
- 观察：no two-tiered standard-rate remark -> progressive-rate path chosen
- 截图：[screenshots/02_stc_2025_26_input.png](screenshots/02_stc_2025_26_input.png)
- 截图：[screenshots/02_stc_2025_26_result.png](screenshots/02_stc_2025_26_result.png)

### 1.3 2026/27（st_comp_2026_27/stc.htm）

- **URL**：https://www.ird.gov.hk/eng/ese/st_comp_2026_27/stc.htm
- **时间戳**：2026-10-13T09:48:01+08:00 → 2026-10-13T09:48:11+08:00（success）
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
- **对照**：✅ 一致（final 口径；before/reduction 同表见 §4）
- 观察：official page displays a single 'TAX PAYABLE BY YOU' row (no reduction rows - no 2026/27 Schedule 43 entry); before=final=value, reduction=0
- 观察：parsed from displayed rows: before_reduction=3160, final_after_reduction=3160
- 观察：no two-tiered standard-rate remark -> progressive-rate path chosen
- 截图：[screenshots/03_stc_2026_27_input.png](screenshots/03_stc_2026_27_input.png)
- 截图：[screenshots/03_stc_2026_27_result.png](screenshots/03_stc_2026_27_result.png)

## 2. GovHK 印花税（物业从价 AVD）计算器

GovHK 印花税入口页本身不含计算表单，页面内链接指向 IRD sd_comp 计算器；本次按入口页链接进入 sdpt_s2_2026.htm（住宅 AVD，Scale 1 Part 1 ＋ Scale 2）。该官方计算器无『文书日期』输入，按其页面说明只计算现行印花税；2026-02-26 起的现行住宅档位在 ≤21,739,120 区间与 2025-02-26 起的 Table 9 完全同档，故案例②（2025-06-01，4,323,780）官方现行计算器的输出与 2025 制度下的期望值可比。该口径限制如实记录。

入口页驱动：https://www.gov.hk/en/residents/taxes/etax/services/stamp_duty_computation.htm（success，2026-10-13T09:48:11+08:00）；点击页面内链接『Ad Valorem Stamp Duty (AVD) (Part 1 of Scale 1 and Scale 2) - residential property』进入 sdpt_s2_2026.htm。落地 URL 记录：link opened the calculator in a new tab (popup)。
截图：[screenshots/04_govhk_entry.png](screenshots/04_govhk_entry.png)
截图：[screenshots/04_govhk_calculator.png](screenshots/04_govhk_calculator.png)

### 2.1 案例①：2026-06-01 住宅 100,000,000（任务期望 4,250,000）

- **URL**：https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm
- **时间戳**：2026-10-13T09:49:50+08:00 → 2026-10-13T09:49:53+08:00（success）
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
- **时间戳**：2026-10-13T09:49:53+08:00 → 2026-10-13T09:49:55+08:00（success）
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
- **时间戳**：2026-10-13T09:49:55+08:00 → 2026-10-13T09:49:59+08:00（success）
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

## 4. 官方输出 × 本引擎 对照汇总表

| 案例 | 官方页面显示值 | 读取方式 | 本引擎值 | 是否一致 | 时间戳（HKT） |
| --- | --- | --- | --- | --- | --- |
| salaries 2024/25（240k/9k/单身） | 2,440 | 官方 eoutdis.htm 结果框架 DOM 文本（_salary_rows 解析 'TAX PAYABLE BY YOU' 各行）＋截图 | 2,440 | ✅ 一致 | 2026-10-13T09:47:43+08:00 → 2026-10-13T09:47:52+08:00 |
| salaries 2025/26（同上） | 940 | 同上 | 940 | ✅ 一致 | 2026-10-13T09:47:52+08:00 → 2026-10-13T09:48:01+08:00 |
| salaries 2026/27（同上） | 3,160 | 同上（该年度无宽减行，官方仅显示单行 'TAX PAYABLE BY YOU'） | 3,160 | ✅ 一致 | 2026-10-13T09:48:01+08:00 → 2026-10-13T09:48:11+08:00 |
| AVD 案例① 2026-06-01 住宅 1 亿 | 4,250,000 | 官方结果只读输入框 input[name=T2] 的显示值 ＋截图 | 4,250,000 | ✅ 一致 | 2026-10-13T09:49:50+08:00 → 2026-10-13T09:49:53+08:00 |
| AVD 案例② 2025-06-01 住宅 4,323,780 | 64,856 | 同上 | 64,856 | ✅ 一致 | 2026-10-13T09:49:53+08:00 → 2026-10-13T09:49:55+08:00 |
| 租约 6 个月总租 60,000 | 150 | 官方结果只读输入框 input[name=T7] 的显示值 ＋截图 | 150 | ✅ 一致 | 2026-10-13T09:49:55+08:00 → 2026-10-13T09:49:59+08:00 |

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

## 5. 方法与限制（含失败尝试，全部如实记录）

### 5.1 失败尝试（本机网络）

| 尝试 | 结果 |
| --- | --- |
| 直接 HTTPS（python urllib / curl） | TLS 握手超时：本机 TUN 代理为 fake-ip DNS，www.ird.gov.hk / www.gov.hk 被解析到 198.18.4.x 假地址；任何 SNI 含 *.gov.hk 的 TLS 连接都停在握手阶段 |
| 经本地混合代理 127.0.0.1:17890（HTTP CONNECT） | CONNECT 隧道建立（HTTP/1.1 200）后上游 TLS 超时；对照 google/github/baidu 经同一代理均 200，说明是代理规则对 *.gov.hk 的路径故障，而非代理整体不可用 |
| 换公共 DNS（8.8.8.8 / 223.5.5.5 UDP:53） | 全部返回同一 fake-ip 198.18.4.x；ipconfig /flushdns 后无变化 |
| webfetch 工具（服务端抓取） | 返回证书校验错误/超时，亦不可用 |
| 真实 IP + SNI（curl --resolve www.ird.gov.hk:443:43.159.109.162） | 仍超时：TUN 按嗅探到的 SNI 域名路由，重新落入故障规则 |
| IPv6 直连真实 AAAA（240d:c010::/32，腾讯 EdgeOne） | 立即失败（本机 IPv6 出口不通/被 TUN 接管） |
| 明文 HTTP 直连真实 IP（Host 头正常） | 仅返回 301 → https，无内容，不可单独使用 |

### 5.2 最终可行的传输变通（如实声明）

可行路径（本脚本实现）：先经本地代理调用 networkcalc.com 的 DNS API 拿到官方站点真实边缘 IP（腾讯 EdgeOne，本次 43.159.109.162；每次运行时实时解析，失败才回退到本次已核验的记录值），再以『无 SNI 的 TLS + 原始 Host 头』直连该 IP 抓取官方页面（curl 验证 200/官方字节数一致；Python 实现见 transport.NoSniHTTPSConnection：wrap_socket(server_hostname=None) 即不发送 SNI）。Playwright 侧用 context.route('**/*') 拦截浏览器请求，以实时抓回的官方字节 fulfill——浏览器仍以官方 origin 加载并执行原始 HTML/CSS/JS，仅传输段被等效替代；页面内容不落盘、不缓存、无任何改动。

影响评估：页面字节与官方服务器逐字节一致（同一次 GET 的响应），浏览器按官方 origin 加载并执行；唯一被替代的是本机到官方服务器之间的传输路径。无 SNI 仅是 TLS 客户端行为，服务器侧（EdgeOne）按 Host 头正常服务，返回内容与浏览器直连完全相同。

### 5.3 页面级变通

stc.htm（薪俸税）为旧式 frameset 设计：initPage()→restorefrmIn() 依赖 frameset 提供的 parent.frmInStatus，DisplayResult() 跳转 parent.main.location.href=“eoutdis.htm”，单开时两者皆缺。处理方式＝最小 parent shim（任务预授权）：①先调用官方自身的 savefrmIn() 把『未填写的初始表单』快照进 parent.frmInStatus，再刷新页面让官方 initPage() 正常跑完（含 GetDeduction() 设定年度全局量）；②补一个空 <iframe name="main"> 作为官方结果页 eoutdis.htm 的落点。计算与结果渲染全部为官方原始代码，未修改任何页面逻辑；结果从该框架内官方渲染的可见文本读取。

### 5.4 官方计算器口径限制

GovHK 印花税入口页本身不含计算表单，页面内链接指向 IRD sd_comp 计算器；本次按入口页链接进入 sdpt_s2_2026.htm（住宅 AVD，Scale 1 Part 1 ＋ Scale 2）。该官方计算器无『文书日期』输入，按其页面说明只计算现行印花税；2026-02-26 起的现行住宅档位在 ≤21,739,120 区间与 2025-02-26 起的 Table 9 完全同档，故案例②（2025-06-01，4,323,780）官方现行计算器的输出与 2025 制度下的期望值可比。该口径限制如实记录。

官方租约计算器（csdta.htm，§1.14 台账 URL）自身约定：租期 ≤1 年必须输入『全期总租金』，月租输入会被页面弹窗拒绝（本次实测留证）。故官方驱动采用等价输入：总租金 60,000（＝月租 10,000 × 6 个月），租期 2025-07-01..2025-12-31；本引擎侧 facts 形状按 tests/engines/stamps/test_lease.py 的 rent_input_mode='total_rent' 同口径。

### 5.5 本次运行的传输日志（逐请求）

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
GET https://www.gov.hk/applications/user_exp/feedback.js?20210430.101915 -> 200 (6485 bytes)
GET https://www.gov.hk/applications/uss_bd/uss_bd.js?20200904.101013 -> 200 (33391 bytes)
GET https://www.gov.hk/applications/survey_banner/health/survey_banner.js?20240508.155042 -> 200 (15850 bytes)
GET https://www.gov.hk/applications/uss_govhk/uss_govhk.js?20241106.164623 -> 200 (15734 bytes)
GET https://www.gov.hk/js/prepost/webstat_tracking.js?20210216.114831 -> 200 (1174 bytes)
GET https://www.gov.hk/js/libs/piwik.js?20230621.175128 -> 200 (61879 bytes)
GET https://www.gov.hk/images/common_icons/orange/icon_service_tax_orange.svg -> 200 (1604 bytes)
GET https://www.gov.hk/applications/uss_govhk/survey_data.json -> 200 (205319 bytes)
GET https://www.gov.hk/images/common_icons/orange/icon_lang_orange.svg -> 200 (2281 bytes)
GET https://www.gov.hk/images/common_icons/white/icon_arrow_down_white.svg -> 200 (642 bytes)
GET https://www.gov.hk/images/common_icons/black/icon_arrow_right_black.svg -> 200 (630 bytes)
GET https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm -> 200 (9245 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/common/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm -> 200 (9245 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/common/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm -> 200 (9245 bytes)
GET https://www.ird.gov.hk/eng/ese/image/banner.gif -> 200 (6367 bytes)
GET https://www.ird.gov.hk/common/images/copy.gif -> 200 (86 bytes)
GET https://www.ird.gov.hk/chi/ese/sd_comp/csdta.htm -> 200 (25802 bytes)
GET https://www.ird.gov.hk/chi/ese/image/banner.gif -> 200 (5075 bytes)
GET https://www.ird.gov.hk/images/copy.gif -> 200 (86 bytes)
```

## 6. 复现方式

```powershell
# 仓库根目录；端到端重跑（结果应可复现，时间戳与网络抖动除外）
.venv\Scripts\python.exe -m scripts.official_calc_compare.run_all
```

脚本目录：`scripts/official_calc_compare/`（transport.py 传输层、drivers.py 页面驱动、local_engine.py 引擎对照、run_all.py 编排与报告生成）。网络仅访问 www.ird.gov.hk / www.gov.hk（官方域名）与 networkcalc.com（DNS 解析 API）。
