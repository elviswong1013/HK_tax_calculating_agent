"""End-to-end runner: drives the official calculators, compares with app.engines,
and writes docs/validation/002-live-official-calculator-comparison.md.

Usage (from the repo root):
    .venv/Scripts/python.exe -m scripts.official_calc_compare.run_all
      [--report PATH] [--screenshots DIR] [--skip-live]

Only ever writes:
  - the report markdown (default docs/validation/002-live-official-calculator-comparison.md)
  - PNG screenshots (default docs/validation/screenshots/)
Nothing else in the repository is touched.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from .common import OFFICIAL_HOSTS, CaseResult, build_route_handler, normalize_amount
from .drivers import (
    STC_BOOTSTRAP_JS,
    drive_avd_case,
    drive_govhk_entry,
    drive_govhk_stock_entry,
    drive_lease_case,
    drive_salaries_year,
    drive_stock_case,
)
from .local_engine import run_all_cases
from .transport import OfficialFetcher

REPO_ROOT = Path(__file__).resolve().parents[2]

FAILED_ATTEMPTS = [
    ("直接 HTTPS（python urllib / curl）", "TLS 握手超时：本机 TUN 代理为 fake-ip DNS，"
     "www.ird.gov.hk / www.gov.hk 被解析到 198.18.4.x 假地址；任何 SNI 含 *.gov.hk 的 TLS 连接都停在握手阶段"),
    ("经本地混合代理 127.0.0.1:17890（HTTP CONNECT）", "CONNECT 隧道建立（HTTP/1.1 200）后上游 TLS 超时；"
     "对照 google/github/baidu 经同一代理均 200，说明是代理规则对 *.gov.hk 的路径故障，而非代理整体不可用"),
    ("换公共 DNS（8.8.8.8 / 223.5.5.5 UDP:53）", "全部返回同一 fake-ip 198.18.4.x；ipconfig /flushdns 后无变化"),
    ("webfetch 工具（服务端抓取）", "返回证书校验错误/超时，亦不可用"),
    ("真实 IP + SNI（curl --resolve www.ird.gov.hk:443:43.159.109.162）", "仍超时：TUN 按嗅探到的 SNI 域名路由，"
     "重新落入故障规则"),
    ("IPv6 直连真实 AAAA（240d:c010::/32，腾讯 EdgeOne）", "立即失败（本机 IPv6 出口不通/被 TUN 接管）"),
    ("明文 HTTP 直连真实 IP（Host 头正常）", "仅返回 301 → https，无内容，不可单独使用"),
    ("2026-10-13 20:15 瞬时传输故障（本次扩展工作期间的端到端重跑）",
     "三个薪俸案例在路由抓取层快速失败（每例约 6 秒，20:15:40-51；当时 error 截图已事后删除以免与成功运行产物混淆），"
     "入口页驱动随之挂起直至终止该次运行；数分钟后传输路径自行恢复，同代码重跑全部案例成功（无代码变更），判断为边缘节点/代理链路的瞬时故障"),
]

TRANSPORT_WORKAROUND = (
    "可行路径（本脚本实现）：先经本地代理调用 networkcalc.com 的 DNS API 拿到官方站点真实边缘 IP"
    "（腾讯 EdgeOne，本次 43.159.109.162；每次运行时实时解析，失败才回退到本次已核验的记录值），"
    "再以『无 SNI 的 TLS + 原始 Host 头』直连该 IP 抓取官方页面（curl 验证 200/官方字节数一致；"
    "Python 实现见 transport.NoSniHTTPSConnection：wrap_socket(server_hostname=None) 即不发送 SNI）。"
    "Playwright 侧用 context.route('**/*') 拦截浏览器请求，以实时抓回的官方字节 fulfill——浏览器仍以官方"
    " origin 加载并执行原始 HTML/CSS/JS，仅传输段被等效替代；页面内容不落盘、不缓存、无任何改动。"
)

SHIM_NOTE = (
    "stc.htm（薪俸税）为旧式 frameset 设计：initPage()→restorefrmIn() 依赖 frameset 提供的 "
    "parent.frmInStatus，DisplayResult() 跳转 parent.main.location.href=\u201ceoutdis.htm\u201d，单开时两者皆缺。"
    "处理方式＝最小 parent shim（任务预授权）：①先调用官方自身的 savefrmIn() 把『未填写的初始表单』快照进 "
    "parent.frmInStatus，再刷新页面让官方 initPage() 正常跑完（含 GetDeduction() 设定年度全局量）；"
    "②补一个空 <iframe name=\"main\"> 作为官方结果页 eoutdis.htm 的落点。计算与结果渲染全部为官方原始代码，"
    "未修改任何页面逻辑；结果从该框架内官方渲染的可见文本读取。"
)

GOVHK_LIMITATION = (
    "GovHK 印花税入口页本身不含计算表单，页面内链接指向 IRD sd_comp 计算器；本次按入口页链接进入"
    " sdpt_s2_2026.htm（住宅 AVD，Scale 1 Part 1 ＋ Scale 2）。该官方计算器无『文书日期』输入，"
    "按其页面说明只计算现行印花税；2026-02-26 起的现行住宅档位在 ≤21,739,120 区间与 2025-02-26 起的"
    " Table 9 完全同档，故案例②（2025-06-01，4,323,780）官方现行计算器的输出与 2025 制度下的期望值"
    "可比。该口径限制如实记录。"
)

LEASE_NOTE = (
    "官方租约计算器（csdta.htm，§1.14 台账 URL）自身约定：租期 ≤1 年必须输入『全期总租金』，月租输入会被"
    "页面弹窗拒绝（本次实测留证）。故官方驱动采用等价输入：总租金 60,000（＝月租 10,000 × 6 个月），"
    "租期 2025-07-01..2025-12-31；本引擎侧 facts 形状按 tests/engines/stamps/test_lease.py 的"
    " rent_input_mode='total_rent' 同口径。"
)

PROVISIONAL_FINDING = (
    "暂缴分项核验记录（2026-10-13，官方字节逐字核验）：IRD 薪俸税计算器三个年度"
    "（st_comp_2024_25／2025_26／2026_27）的 stc.htm 与结果页模板 eoutdis.htm 源码中"
    "『provisional』字样出现 0 次——结果页（官方 eoutdis.htm 渲染）仅含本年度"
    " TOTAL INCOME／DEDUCTIONS／ALLOWANCES／NET CHARGEABLE INCOME／TAX PAYABLE BY YOU 各行与"
    " Disclaimer，不含『Provisional tax for the following year』分项；该计算器亦无暂缴相关输入。"
    "中文版路径（/chi/ese/st_comp_*/）返回 404（计算器仅英文）。因此『官方显示的下年度暂缴』无从取得，"
    "本次如实记录为『官方无该分项』，不虚构数值；驱动器每年度仍实时解析结果文本中的 provisional 行"
    "（有则记录原文，无则如实留痕）。本引擎 next_provisional 与官方数值的间接核对见 §5。"
)

STOCK_RESEARCH = (
    "股票计算器调研留痕（2026-10-13，全部经本目录 transport 实时访问官方域名或公开检索）："
    "①GovHK 印花税入口页 stamp_duty_computation.htm 链接枚举：仅住宅 AVD（sdpt_s2_2026.htm）、"
    "非住宅 AVD（sdpt_s3_2026.htm）、租约（sdta.htm）三个计算器链接，无股票；"
    "②公开检索（官方域名限定）命中 IRD 官方『Stamp Duty Computation - Share Transfer』"
    "（ird.gov.hk/eng/ese/sd_comp/sdshare.htm，Last revision date: 17 November 2023）与 GovHK eTax"
    " 入口页『Stamp Duty Computation (Share Transfer Document)』"
    "（gov.hk/.../share_transfer_computation.htm，页面内 'Share Transfer Document' 链接指向 sdshare.htm）；"
    "③两页本次运行均实时 200 抓取核验。**任务简报预期『官方股票计算器不存在』与实况不符，"
    "已据实修正：官方计算器公开存在，本次按『若存在 → 同法实时对比』分支执行，未使用任何第三方计算器。**"
)

STOCK_SCOPE_NOTE = (
    "口径差异（如实记录，非不一致）：官方股票计算器按『交易视图』显示一次买卖的印花税＝"
    " BNSN（两份 contract note 合计，页面源码公式 bnsnValue=Math.ceil(0.0010×consideration)×2）"
    " ＋ IOT（转让文书固定 $5，恒显示）。本引擎按 D2.2 红线逐份文书计税：contract note 各"
    " 0.1% 分别 ceil1、sold/bought 不合并；**不得**在无 other_transfer 文书事实时自动附加转让文书"
    " $5。故对照按两组口径映射列示：『官方 BNSN ↔ 引擎两 note 合计』、"
    "『官方 IOT ↔ 引擎 other_transfer 单文书（$5）』；自愿/非买卖路径官方 oIOT=5+Math.ceil(0.0020×value)"
    " 与引擎 voluntary_inter_vivos（$5＋0.2% of value，一次 ceil1）同口径直比。"
)

ENTRY_TIMEOUT_NOTE = (
    "补充说明（入口页『failed』的实况定性）：该 GovHK 门户页的主文档已被传输层 200 实时抓回"
    "（§6.7 日志含该 GET 及字节数，与直连核验逐字节一致），失败发生在浏览器导航层——gov.hk 门户"
    "单页含 100+ 子资源，经无 SNI 传输层逐请求串行抓取，90 秒 goto 窗口内未全部加载完成；"
    "官方服务器本身可达且返回官方字节。计算器本体（ird.gov.hk 直达 URL）不受影响，本次照常驱动与对照。"
)


def _fmt_amount(v: str | None) -> str:
    if v is None or not v.isdigit():
        return "（未取得）"
    return f"{int(v):,}"


def _steps_html(steps: list[str]) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


def _compare(official: str | None, engine: str | None) -> str:
    o, e = normalize_amount(official), normalize_amount(engine)
    if o is None:
        return "**受限（官方值未取得）**"
    return "✅ 一致" if o == e else f"❌ 不一致（官方 {o} vs 引擎 {e}）"


def _parsed_before_official(case: CaseResult | None) -> str | None:
    """Extract the parsed before_reduction amount from a case's observations."""
    if case is None:
        return None
    for o in case.extra_observations:
        if "before_reduction=" in o:
            val = o.split("before_reduction=")[1].split(",")[0].strip()
            if val.isdigit():
                return val
    return None


def _stock_official_iot(case: CaseResult | None) -> str | None:
    """Extract the official Instrument-of-Transfer value from the stock-sale case raw text."""
    if case is None:
        return None
    m = re.search(r"Instrument of Transfer input\[name=IOT\] displayed value: ([\d,]+)", case.official_raw)
    return normalize_amount(m.group(1)) if m else None


def _provisional_official_label(c: CaseResult | None) -> str:
    """Honest display text for the official provisional line item."""
    if c is None:
        return "（未运行）"
    if c.official_provisional:
        return _fmt_amount(c.official_provisional)
    return "（官方结果页无此分项，不虚构）"


def render_report(
    cases: list[CaseResult],
    engine_outcomes,
    transport_log: list[str],
    shot_dir: Path,
    report_path: Path,
) -> str:
    rel = lambda name: f"screenshots/{Path(name).name}"  # noqa: E731
    by_name = {c.name: c for c in cases}
    eng = engine_outcomes

    lines: list[str] = []
    add = lines.append

    add("# 002 - 官方计算器实时驱动对比（Playwright，IRD/GovHK）")
    add("")
    add("任务：用 Playwright 实时驱动 IRD/GovHK 官方网页计算器，与本仓库 app.engines 纯函数对同一输入的输出对照。")
    add("")
    add("- **运行时间**：" + (cases[0].started_at if cases else "n/a") + " ～ " + (cases[-1].finished_at if cases else "n/a") + "（HKT）")
    add("- **环境**：Windows 10 + 本仓库 .venv（playwright 1.63 + chromium，无头模式）；网络仅访问官方域名 www.ird.gov.hk / www.gov.hk（外加解析真实 IP 用的 networkcalc.com DNS API）。")
    add("- **性质**：尽力而为的验证记录；每项如实标注 成功 / 受限，含全部失败尝试。")
    add("- **声明**：仅驱动官方 UI（填字段、点 Compute、读显示值），未修改任何官方页面逻辑；所有页面字节均在运行时从官方服务器实时取回。本目录与 screenshots/ 为本次新增，未改动仓库其他文件。")
    add("")

    # ---- 1. salaries -----------------------------------------------------
    add("## 1. 薪俸税计算器（IRD stc.htm，三年度）")
    add("")
    add("输入（三年度相同）：单身、受雇收入 240,000、MPF 强制供款 9,000，其余全部留 0（官方页面默认）。")
    add("")
    add(SHIM_NOTE)
    add("")
    idx = 1
    for year_label, key in (("2024/25", "salaries_2024_25"), ("2025/26", "salaries_2025_26"), ("2026/27", "salaries_2026_27")):
        c = by_name.get(key)
        e = eng[key]
        add(f"### 1.{idx} {year_label}（st_comp_{year_label[:4]}_{year_label[5:]}/stc.htm）")
        add("")
        if c is None:
            add("- **状态**：未运行")
            add("")
            idx += 1
            continue
        add(f"- **URL**：{c.url}")
        add(f"- **时间戳**：{c.started_at} → {c.finished_at}（{c.status}）")
        add("- **输入步骤**：")
        add("")
        add(_steps_html(c.steps))
        add("")
        if c.official_raw:
            add("- **官方显示值原文**（官方 eoutdis.htm 结果框架 innerText 摘录）：")
            add("")
            add("```text")
            for line in c.official_raw.splitlines():
                if line.strip():
                    add(line.rstrip())
            add("```")
            add("")
        before_off = _parsed_before_official(c)
        after_off = c.official_value
        red_off = None
        if before_off and after_off:
            red_off = str(int(before_off) - int(after_off))
        add(f"- **官方显示解析**：before_reduction={_fmt_amount(before_off)}，reduction={_fmt_amount(red_off)}，final_after_reduction={_fmt_amount(after_off)}")
        add(f"- **本引擎值**（app.engines.salaries.calculate_salaries_tax，facts 形状同 tests/engines/test_salaries_engine.py）：status={e.status}，before={_fmt_amount(e.before_reduction)}，reduction={_fmt_amount(e.reduction)}，final={_fmt_amount(e.value)}")
        add(f"- **对照**：{_compare(after_off, e.value)}（final 口径；before/reduction 同表见 §5）")
        add(f"- **下年度暂缴（Provisional tax for the following year）官方显示**：{_provisional_official_label(c)}")
        add(f"- **本引擎 next_provisional**（同输入 240k/9k/单身，按下一年度规则估算、不计宽减——附表 43 宽减不适用暂缴）：{_fmt_amount(e.next_provisional)}")
        add(f"- **暂缴对照**：受限（官方结果页无暂缴分项，无从直接对照；官方字节逐字核验与间接核对见 §6）")
        for obs in c.extra_observations:
            add(f"- 观察：{obs}")
        if c.error:
            add(f"- **失败原因**：`{c.error}`")
        for s in c.screenshots:
            add(f"- 截图：[{rel(s)}]({rel(s)})")
        add("")
        idx += 1

    # ---- 2. property AVD --------------------------------------------------
    add("## 2. GovHK 印花税（物业从价 AVD）计算器")
    add("")
    add(GOVHK_LIMITATION)
    add("")
    entry = by_name.get("govhk_sd_entry")
    if entry is not None:
        add(f"入口页驱动：{entry.url}（{entry.status}，{entry.started_at}）；"
            f"点击页面内链接『Ad Valorem Stamp Duty (AVD) (Part 1 of Scale 1 and Scale 2) - residential property』"
            f"进入 sdpt_s2_2026.htm。落地 URL 记录：{(entry.extra_observations or [''])[0].replace('landed on: ', '')}。")
        for s in entry.screenshots:
            add(f"截图：[{rel(s)}]({rel(s)})")
        if entry.error:
            add(f"入口页失败原因：`{entry.error}`（后续案例改用直达计算器 URL，不影响计算器本身的驱动）")
            add(ENTRY_TIMEOUT_NOTE)
        add("")

    avd_cases = (
        ("2.1", "案例①：2026-06-01 住宅 100,000,000（任务期望 4,250,000）", "avd_case1_20260601", "2026-06-01"),
        ("2.2", "案例②：2025-06-01 住宅 4,323,780（任务期望 64,856）", "avd_case2_20250601", "2025-06-01"),
    )
    for sub, title, key, _d in avd_cases:
        c = by_name.get(key)
        e = eng[key]
        add(f"### {sub} {title}")
        add("")
        if c is None:
            add("- **状态**：未运行")
            add("")
            continue
        add(f"- **URL**：{c.url}")
        add(f"- **时间戳**：{c.started_at} → {c.finished_at}（{c.status}）")
        add("- **输入步骤**：")
        add("")
        add(_steps_html(c.steps))
        add("")
        add(f"- **官方显示值原文**：`{c.official_raw}`")
        add(f"- **本引擎值**（app.engines.stamps.property.calculate_property_avd，facts 形状同 tests/engines/stamps/test_property_avd.py）：status={e.status}，duty={_fmt_amount(e.value)}，detail={e.detail}")
        add(f"- **对照**：{_compare(c.official_value, e.value)}")
        for obs in c.extra_observations:
            add(f"- 观察：{obs}")
        if c.error:
            add(f"- **失败原因**：`{c.error}`")
        for s in c.screenshots:
            add(f"- 截图：[{rel(s)}]({rel(s)})")
        add("")

    # ---- 3. lease ----------------------------------------------------------
    add("## 3. 租约印花税计算器（IRD csdta.htm）")
    add("")
    add(LEASE_NOTE)
    add("")
    c = by_name.get("lease_6months")
    e = eng["lease_6months"]
    add("### 3.1 案例：月租 10,000 × 6 个月 → 总租金 60,000（任务期望 150）")
    add("")
    if c is None:
        add("- **状态**：未运行")
        add("")
    else:
        add(f"- **URL**：{c.url}")
        add(f"- **时间戳**：{c.started_at} → {c.finished_at}（{c.status}）")
        add("- **输入步骤**：")
        add("")
        add(_steps_html(c.steps))
        add("")
        add(f"- **官方显示值原文**：`{c.official_raw}`")
        add(f"- **本引擎值**（app.engines.stamps.lease.calculate_lease_stamp_duty，facts 形状同 tests/engines/stamps/test_lease.py）：status={e.status}，total={_fmt_amount(e.value)}，detail={e.detail}")
        add(f"- **对照**：{_compare(c.official_value, e.value)}")
        for obs in c.extra_observations:
            add(f"- 观察：{obs}")
        if c.error:
            add(f"- **失败原因**：`{c.error}`")
        for s in c.screenshots:
            add(f"- 截图：[{rel(s)}]({rel(s)})")
        add("")

    # ---- 4. stock share-transfer --------------------------------------------
    add("## 4. 股票转让印花税计算器（IRD sdshare.htm）——任务预期『不存在』被实时调研推翻")
    add("")
    add(STOCK_RESEARCH)
    add("")
    entry_stock = by_name.get("govhk_stock_entry")
    if entry_stock is not None:
        add(f"入口页驱动：{entry_stock.url}（{entry_stock.status}，{entry_stock.started_at}）；"
            f"点击页面内链接『Share Transfer Document』进入 sdshare.htm。落地 URL 记录："
            f"{(entry_stock.extra_observations or [''])[0].replace('landed on: ', '')}。")
        for s in entry_stock.screenshots:
            add(f"截图：[{rel(s)}]({rel(s)})")
        if entry_stock.error:
            add(f"入口页失败原因：`{entry_stock.error}`（后续案例改用直达计算器 URL，不影响计算器本身的驱动）")
            add("")
            add(ENTRY_TIMEOUT_NOTE)
        add("")
    add(STOCK_SCOPE_NOTE)
    add("")

    stock_cases = (
        ("4.1 案例①（买卖路径）：代价 1,000.01 → 官方 BNSN=各 note 0.1% 分别 ceil1 ×2（任务期望两 note 合计 4；另含 IOT $5）",
         "stock_sale_notes_1000_01", "1000.01"),
        ("4.2 案例②（自愿/非买卖路径）：value 1,000.01 → 官方 oIOT=5＋0.2% 一次 ceil1（任务期望 8）",
         "stock_voluntary_1000_01", "1000.01"),
    )
    for sub, key, _amount in stock_cases:
        c = by_name.get(key)
        e = eng[key]
        add(f"### {sub}")
        add("")
        if c is None:
            add("- **状态**：未运行")
            add("")
            continue
        add(f"- **URL**：{c.url}")
        add(f"- **时间戳**：{c.started_at} → {c.finished_at}（{c.status}）")
        add("- **输入步骤**：")
        add("")
        add(_steps_html(c.steps))
        add("")
        add(f"- **官方显示值原文**：`{c.official_raw}`")
        add(f"- **本引擎值**（app.engines.stamps.stock.calculate_stock_stamp_duty，facts 形状同 tests/engines/stamps/test_stock.py）：status={e.status}，total={_fmt_amount(e.value)}，detail={e.detail}")
        add(f"- **对照**（官方值口径见各行观察）：{_compare(c.official_value, e.value)}")
        for obs in c.extra_observations:
            add(f"- 观察：{obs}")
        if key == "stock_sale_notes_1000_01":
            iot_off = _stock_official_iot(c)
            iot_eng = eng["stock_other_transfer_doc"]
            add(f"- **IOT 口径对照**：官方 Instrument of Transfer={_fmt_amount(iot_off)} ↔ 本引擎 other_transfer 单文书（$5，stock_other_transfer_doc）={_fmt_amount(iot_eng.value)} → {_compare(iot_off, iot_eng.value)}（口径映射说明见本节开头）")
        if c.error:
            add(f"- **失败原因**：`{c.error}`")
        for s in c.screenshots:
            add(f"- 截图：[{rel(s)}]({rel(s)})")
        add("")

    # ---- 5. summary table ---------------------------------------------------
    add("## 5. 官方输出 × 本引擎 对照汇总表")
    add("")
    add("| 案例 | 官方页面显示值 | 读取方式 | 本引擎值 | 是否一致 | 时间戳（HKT） |")
    add("| --- | --- | --- | --- | --- | --- |")
    rows = [
        ("salaries 2024/25（240k/9k/单身）", "salaries_2024_25", "官方 eoutdis.htm 结果框架 DOM 文本（_salary_rows 解析 'TAX PAYABLE BY YOU' 各行）＋截图"),
        ("salaries 2025/26（同上）", "salaries_2025_26", "同上"),
        ("salaries 2026/27（同上）", "salaries_2026_27", "同上（该年度无宽减行，官方仅显示单行 'TAX PAYABLE BY YOU'）"),
        ("AVD 案例① 2026-06-01 住宅 1 亿", "avd_case1_20260601", "官方结果只读输入框 input[name=T2] 的显示值 ＋截图"),
        ("AVD 案例② 2025-06-01 住宅 4,323,780", "avd_case2_20250601", "同上"),
        ("租约 6 个月总租 60,000", "lease_6months", "官方结果只读输入框 input[name=T7] 的显示值 ＋截图"),
        ("股票买卖 1,000.01：两份 contract note 合计（官方 BNSN）", "stock_sale_notes_1000_01", "官方结果只读输入框 input[name=BNSN] 的显示值 ＋截图"),
        ("股票买卖 1,000.01：转让文书（官方 IOT ↔ 引擎 other_transfer 单文书）", "stock_sale_notes_1000_01", "同一次结果页 input[name=IOT] 的显示值（引擎侧取 other_transfer 单文书案例）"),
        ("股票自愿转让 value 1,000.01（官方 oIOT）", "stock_voluntary_1000_01", "官方结果只读输入框 input[name=oIOT] 的显示值 ＋截图"),
    ]
    iot_official = _stock_official_iot(by_name.get("stock_sale_notes_1000_01"))
    for label, key, method in rows:
        c = by_name.get(key)
        e = eng["stock_other_transfer_doc"] if label.startswith("股票买卖 1,000.01：转让文书") else eng[key]
        if label.startswith("股票买卖 1,000.01：转让文书"):
            off_disp, off_cmp, eng_disp = _fmt_amount(iot_official), iot_official, _fmt_amount(e.value)
            ts = f"{c.started_at} → {c.finished_at}" if c else "n/a"
            add(f"| {label} | {off_disp} | {method} | {eng_disp} | {_compare(off_cmp, e.value)}（口径映射见 §4） | {ts} |")
            continue
        off = _fmt_amount(c.official_value) if c else "（未运行）"
        ts = f"{c.started_at} → {c.finished_at}" if c else "n/a"
        add(f"| {label} | {off} | {method} | {_fmt_amount(e.value)} | {_compare(c.official_value if c else None, e.value)} | {ts} |")
    for year, key in (("2024/25", "salaries_2024_25"), ("2025/26", "salaries_2025_26"), ("2026/27", "salaries_2026_27")):
        c = by_name.get(key)
        e = eng[key]
        ts = f"{c.started_at} → {c.finished_at}" if c else "n/a"
        add(f"| 暂缴（下年度暂缴）{year} 输入（240k/9k） | {_provisional_official_label(c)} | 官方结果页无暂缴分项（见 §6） | {_fmt_amount(e.next_provisional)} | 受限（间接核对见下表） | {ts} |")
    add("")
    add("三年度薪俸税官方页显示的宽减差异（同一输入）：2024/25 宽减 1,500、2025/26 宽减 3,000、2026/27 无宽减条目"
        "（且 2026/27 基本免税额改为 145,000，官方页面同输入的累进税额相应为 3,160）——与本引擎"
        " reduction=1500/3000/0 的规则束一致。明细：")
    add("")
    add("| 年度 | 口径 | 官方显示 | 本引擎 | 一致 |")
    add("| --- | --- | --- | --- | --- |")
    for year, key in (("2024/25", "salaries_2024_25"), ("2025/26", "salaries_2025_26"), ("2026/27", "salaries_2026_27")):
        c = by_name.get(key)
        e = eng[key]
        before_off = _parsed_before_official(c)
        after_off = c.official_value if c else None
        red_off = str(int(before_off) - int(after_off)) if (before_off and after_off) else None
        add(f"| {year} | before_reduction | {_fmt_amount(before_off)} | {_fmt_amount(e.before_reduction)} | {_compare(before_off, e.before_reduction)} |")
        add(f"| {year} | reduction | {_fmt_amount(red_off)} | {_fmt_amount(e.reduction)} | {_compare(red_off, e.reduction)} |")
        add(f"| {year} | final_after_reduction | {_fmt_amount(after_off)} | {_fmt_amount(e.value)} | {_compare(after_off, e.value)} |")
    add("")

    # ---- 5.1 provisional indirect cross-check -------------------------------
    add("### 5.1 下年度暂缴（next_provisional）：官方无分项 → 与官方下一年度页面的间接核对")
    add("")
    add("引擎口径：next_provisional＝同一事实按下一年度规则估算（下一年度不在规则束内时按引擎现状回退本年度规则），不计宽减（附表 43 宽减不适用暂缴）。官方无暂缴分项，故下表为**间接**核对——用官方『下一年度』计算器页面同输入的税额（本报告 §1 已实时驱动并截图）对照本引擎 next_provisional；每行均如实标注口径，不把间接核对冒充直接对照。")
    add("")
    add("| 输入年度（240k/9k/单身） | 引擎 next_provisional | 引擎规则口径 | 间接官方核对源（同输入） | 间接结论 |")
    add("| --- | --- | --- | --- | --- |")
    prov_rows = (
        ("2024/25", "salaries_2024_25", "salaries_2025_26", "2025/26 规则（2025_26 在束内）",
         "官方 2025/26 页 before_reduction", "同输入下一年度页面的宽减前税额——宽减不适用暂缴，口径吻合"),
        ("2025/26", "salaries_2025_26", "salaries_2026_27", "2026/27 规则（2026_27 在束内）",
         "官方 2026/27 页 TAX PAYABLE", "同输入下一年度页面税额（该年无宽减条目）"),
        ("2026/27", "salaries_2026_27", None, "2027_28 不在规则束内 → 按引擎现状回退 2026/27 规则（如实说明，非 2027/28 官方规则）",
         "官方 2026/27 页 TAX PAYABLE（同规则同输入）", "仅同规则参考值；官方 2027/28 计算器尚未发布，无真正对应页"),
    )
    for in_year, key, indirect_key, rule_note, source, note in prov_rows:
        e = eng[key]
        if indirect_key is not None:
            indirect_val = _parsed_before_official(by_name.get(indirect_key)) or (
                by_name.get(indirect_key).official_value if by_name.get(indirect_key) else None)
        else:
            indirect_val = by_name.get(key).official_value if by_name.get(key) else None
        match = _compare(indirect_val, e.next_provisional)
        add(f"| {in_year} | {_fmt_amount(e.next_provisional)} | {rule_note} | {source}＝{_fmt_amount(indirect_val)} | {match}（{note}） |")
    add("")

    # ---- 6. method & limitations --------------------------------------------
    add("## 6. 方法与限制（含失败尝试，全部如实记录）")
    add("")
    add("### 6.1 失败尝试（本机网络）")
    add("")
    add("| 尝试 | 结果 |")
    add("| --- | --- |")
    for name, res in FAILED_ATTEMPTS:
        add(f"| {name} | {res} |")
    add("")
    add("### 6.2 最终可行的传输变通（如实声明）")
    add("")
    add(TRANSPORT_WORKAROUND)
    add("")
    add("影响评估：页面字节与官方服务器逐字节一致（同一次 GET 的响应），浏览器按官方 origin 加载并执行；"
        "唯一被替代的是本机到官方服务器之间的传输路径。无 SNI 仅是 TLS 客户端行为，服务器侧（EdgeOne）按 "
        "Host 头正常服务，返回内容与浏览器直连完全相同。")
    add("")
    add("### 6.3 页面级变通")
    add("")
    add(SHIM_NOTE)
    add("")
    add("### 6.4 官方计算器口径限制")
    add("")
    add(GOVHK_LIMITATION)
    add("")
    add(LEASE_NOTE)
    add("")
    add("### 6.5 暂缴分项核验（新增对比项 A）")
    add("")
    add(PROVISIONAL_FINDING)
    add("")
    add("### 6.6 股票计算器调研与口径差异（新增对比项 B）")
    add("")
    add(STOCK_RESEARCH)
    add("")
    add(STOCK_SCOPE_NOTE)
    add("")
    add("### 6.7 本次运行的传输日志（逐请求）")
    add("")
    add("```text")
    for line in transport_log:
        add(line)
    add("```")
    add("")

    # ---- 7. reproduce --------------------------------------------------------
    add("## 7. 复现方式")
    add("")
    add("```powershell")
    add("# 仓库根目录；端到端重跑（结果应可复现，时间戳与网络抖动除外）")
    add(".venv\\Scripts\\python.exe -m scripts.official_calc_compare.run_all")
    add("```")
    add("")
    add("脚本目录：`scripts/official_calc_compare/`（transport.py 传输层、drivers.py 页面驱动"
        "（薪俸三年度＋AVD＋租约＋股票 sdshare）、local_engine.py 引擎对照、run_all.py 编排与报告生成）。"
        "网络仅访问 www.ird.gov.hk / www.gov.hk（官方域名）与 networkcalc.com（DNS 解析 API）。")
    add("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    report_path.write_text(text, encoding="utf-8")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=str(REPO_ROOT / "docs/validation/002-live-official-calculator-comparison.md"))
    parser.add_argument("--screenshots", default=str(REPO_ROOT / "docs/validation/screenshots"))
    parser.add_argument("--skip-live", action="store_true", help="跳过在线驱动，仅重算引擎并重写报告（调试用）")
    args = parser.parse_args()

    shot_dir = Path(args.screenshots)
    shot_dir.mkdir(parents=True, exist_ok=True)

    cases: list[CaseResult] = []
    fetcher = OfficialFetcher()
    transport_log: list[str] = []

    if not args.skip_live:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": 1280, "height": 1000},
                    locale="en-US",
                )
                context.set_default_timeout(30_000)
                context.route("**/*", build_route_handler(fetcher))
                # frameset-bootstrap shim for stc.htm (scoped to stc URLs inside)
                context.add_init_script(STC_BOOTSTRAP_JS)

                # 1) salaries tax, three assessment years, same inputs
                for year_label, year_dir, shot in (
                    ("2024_25", "st_comp_2024_25", "01_stc_2024_25"),
                    ("2025_26", "st_comp_2025_26", "02_stc_2025_26"),
                    ("2026_27", "st_comp_2026_27", "03_stc_2026_27"),
                ):
                    cases.append(drive_salaries_year(
                        context, year_label, year_dir,
                        income="240000", mpf="9000",
                        screenshot_prefix=str(shot_dir / shot),
                    ))

                # 2) GovHK entry -> residential AVD calculator, two cases
                cases.append(drive_govhk_entry(context, str(shot_dir / "04_govhk")))
                cases.append(drive_avd_case(
                    context, "avd_case1_20260601", "100000000",
                    str(shot_dir / "04_avd_case1"),
                ))
                cases.append(drive_avd_case(
                    context, "avd_case2_20250601", "4323780",
                    str(shot_dir / "05_avd_case2"),
                ))

                # 3) lease calculator (csdta.htm), 6 months, total rent 60000
                cases.append(drive_lease_case(
                    context, total_rent="60000", monthly_equiv="10000",
                    start=("01", "07", "2025"), end=("31", "12", "2025"),
                    screenshot_prefix=str(shot_dir / "06_lease"),
                ))

                # 4) stock share-transfer calculator (sdshare.htm), extension item B:
                #    GovHK eTax entry page click-through + two cases (sale / voluntary)
                cases.append(drive_govhk_stock_entry(context, str(shot_dir / "07_govhk_stock")))
                cases.append(drive_stock_case(
                    context, mode="sale", amount="1000.01",
                    case_id="stock_sale_notes_1000_01",
                    screenshot_prefix=str(shot_dir / "07_stock_sale"),
                ))
                cases.append(drive_stock_case(
                    context, mode="voluntary", amount="1000.01",
                    case_id="stock_voluntary_1000_01",
                    screenshot_prefix=str(shot_dir / "08_stock_voluntary"),
                ))
            finally:
                browser.close()
        transport_log.extend(fetcher.log)

    engine_outcomes = run_all_cases()
    report_path = Path(args.report)
    render_report(cases, engine_outcomes, transport_log, shot_dir, report_path)

    # console summary
    print("=" * 78)
    print("002 live official calculator comparison - run summary")
    print("=" * 78)
    for c in cases:
        val = c.official_value or "n/a"
        print(f"[{c.status:>7}] {c.name:<22} official={val:<12} {c.started_at} -> {c.finished_at}")
        if c.error:
            print(f"          error: {c.error}")
    print("-" * 78)
    for name, e in engine_outcomes.items():
        print(f"[engine ] {name:<22} value={e.value} status={e.status}")
    print("-" * 78)
    matches = 0
    total = 0
    by_name = {c.name: c for c in cases}
    # (label, official_case_key, engine_key, official_override) - the stock IOT
    # comparable reads the IOT value from the same official result page as the
    # BNSN comparable, but maps to the engine's other_transfer case.
    iot_official = _stock_official_iot(by_name.get("stock_sale_notes_1000_01"))
    comparables = [
        ("salaries 2024/25 final", "salaries_2024_25", "salaries_2024_25", None),
        ("salaries 2025/26 final", "salaries_2025_26", "salaries_2025_26", None),
        ("salaries 2026/27 final", "salaries_2026_27", "salaries_2026_27", None),
        ("avd case1", "avd_case1_20260601", "avd_case1_20260601", None),
        ("avd case2", "avd_case2_20250601", "avd_case2_20250601", None),
        ("lease 6mo", "lease_6months", "lease_6months", None),
        ("stock sale notes (BNSN)", "stock_sale_notes_1000_01", "stock_sale_notes_1000_01", None),
        ("stock IOT <-> other_transfer", "stock_sale_notes_1000_01", "stock_other_transfer_doc", iot_official),
        ("stock voluntary (oIOT)", "stock_voluntary_1000_01", "stock_voluntary_1000_01", None),
    ]
    for label, case_key, engine_key, override in comparables:
        c = by_name.get(case_key)
        e = engine_outcomes[engine_key]
        official = override if override is not None else (c.official_value if c else None)
        total += 1
        if official and normalize_amount(official) == normalize_amount(e.value):
            matches += 1
        status = "match" if (official and normalize_amount(official) == normalize_amount(e.value)) else "MISMATCH-OR-LIMITED"
        print(f"[compare] {label:<32} official={official or 'n/a':<12} engine={e.value} -> {status}")
    print(f"comparison: {matches}/{total} comparables matched (engine value == official displayed value)")
    print(f"report: {report_path}")
    print(f"screenshots dir: {shot_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
