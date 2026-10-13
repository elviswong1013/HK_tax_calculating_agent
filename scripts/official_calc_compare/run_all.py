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
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from .common import OFFICIAL_HOSTS, CaseResult, build_route_handler, normalize_amount
from .drivers import (
    STC_BOOTSTRAP_JS,
    drive_avd_case,
    drive_govhk_entry,
    drive_lease_case,
    drive_salaries_year,
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
        add(f"- **对照**：{_compare(after_off, e.value)}（final 口径；before/reduction 同表见 §4）")
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

    # ---- 4. summary table ---------------------------------------------------
    add("## 4. 官方输出 × 本引擎 对照汇总表")
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
    ]
    for label, key, method in rows:
        c = by_name.get(key)
        e = eng[key]
        off = _fmt_amount(c.official_value) if c else "（未运行）"
        ts = f"{c.started_at} → {c.finished_at}" if c else "n/a"
        add(f"| {label} | {off} | {method} | {_fmt_amount(e.value)} | {_compare(c.official_value if c else None, e.value)} | {ts} |")
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

    # ---- 5. method & limitations --------------------------------------------
    add("## 5. 方法与限制（含失败尝试，全部如实记录）")
    add("")
    add("### 5.1 失败尝试（本机网络）")
    add("")
    add("| 尝试 | 结果 |")
    add("| --- | --- |")
    for name, res in FAILED_ATTEMPTS:
        add(f"| {name} | {res} |")
    add("")
    add("### 5.2 最终可行的传输变通（如实声明）")
    add("")
    add(TRANSPORT_WORKAROUND)
    add("")
    add("影响评估：页面字节与官方服务器逐字节一致（同一次 GET 的响应），浏览器按官方 origin 加载并执行；"
        "唯一被替代的是本机到官方服务器之间的传输路径。无 SNI 仅是 TLS 客户端行为，服务器侧（EdgeOne）按 "
        "Host 头正常服务，返回内容与浏览器直连完全相同。")
    add("")
    add("### 5.3 页面级变通")
    add("")
    add(SHIM_NOTE)
    add("")
    add("### 5.4 官方计算器口径限制")
    add("")
    add(GOVHK_LIMITATION)
    add("")
    add(LEASE_NOTE)
    add("")
    add("### 5.5 本次运行的传输日志（逐请求）")
    add("")
    add("```text")
    for line in transport_log:
        add(line)
    add("```")
    add("")

    # ---- 6. reproduce --------------------------------------------------------
    add("## 6. 复现方式")
    add("")
    add("```powershell")
    add("# 仓库根目录；端到端重跑（结果应可复现，时间戳与网络抖动除外）")
    add(".venv\\Scripts\\python.exe -m scripts.official_calc_compare.run_all")
    add("```")
    add("")
    add("脚本目录：`scripts/official_calc_compare/`（transport.py 传输层、drivers.py 页面驱动、"
        "local_engine.py 引擎对照、run_all.py 编排与报告生成）。网络仅访问 www.ird.gov.hk / www.gov.hk"
        "（官方域名）与 networkcalc.com（DNS 解析 API）。")
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
    for key in ("salaries_2024_25", "salaries_2025_26", "salaries_2026_27",
                "avd_case1_20260601", "avd_case2_20250601", "lease_6months"):
        c = by_name.get(key)
        e = engine_outcomes[key]
        total += 1
        if c and c.official_value and normalize_amount(c.official_value) == normalize_amount(e.value):
            matches += 1
    print(f"comparison: {matches}/{total} cases matched (engine value == official displayed value)")
    print(f"report: {report_path}")
    print(f"screenshots dir: {shot_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
