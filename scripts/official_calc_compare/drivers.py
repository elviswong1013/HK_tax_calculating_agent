"""Playwright drivers for the three official calculators (task 002).

Only UI driving happens here:
  - fields are filled like a user would,
  - the official "Compute" buttons are clicked,
  - the values the official page displays are read back as text.

Two transport-level workarounds are applied (both recorded in the report):
  1. Requests are fulfilled from live no-SNI fetches (transport.py) because the
     workstation's TUN proxy stalls every TLS connection carrying a *.gov.hk
     SNI.  The browser renders/executes the original official bytes unchanged.
  2. stc.htm (salaries tax) is designed to run inside a frameset: its
     DisplayResult() navigates parent.main.location.href = "eoutdis.htm".
     When opened standalone there is no frame named "main".  We inject a
     minimal shim - an empty <iframe name="main"> - which restores the exact
     navigation the official frameset design intends.  No calculation logic is
     touched; the computation and the result page are entirely official code.
"""
from __future__ import annotations

import re

from playwright.sync_api import Page  # type refs only

from .common import CaseResult, hkt_now, normalize_amount

# Bootstrap for stc.htm: the original frameset wrapper provides
# parent.frmInStatus (read by initPage->restorefrmIn) before the page boots.
# This init script (registered for stc.htm URLs only) creates the array and
# lets the page's own official savefrmIn() fill it with the pristine form
# state as soon as the DOM is parsed - mirroring exactly what the official
# wrapper does for a fresh visit.  No calculation logic is touched.
STC_BOOTSTRAP_JS = r"""
if (/st_comp_[0-9]{4}_[0-9]{2}\/stc\.htm/.test(location.href)) {
  window.frmInStatus = new Array(64);
  window.addEventListener('DOMContentLoaded', function () {
    try {
      window.dF = document.fI;
      savefrmIn();
    } catch (e) {}
  }, { once: true });
}
"""

_SHIM_FRAME_JS = """
() => {
  if (!document.querySelector('iframe[name="main"]')) {
    const f = document.createElement('iframe');
    f.name = 'main';
    f.id = 'result-frame';
    f.style.cssText = 'width:1050px;height:1400px;border:2px solid #555;margin:8px 0';
    document.body.appendChild(f);
  }
  return true;
}
"""

_IFRAME_TEXT_JS = """
() => {
  const f = document.querySelector('iframe#result-frame');
  if (!f) return null;
  try {
    const doc = f.contentWindow.document;
    return doc && doc.body ? doc.body.innerText : null;
  } catch (e) { return null; }
}
"""


class DialogRecorder:
    """Records JS alert()/confirm() messages raised by the official pages."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def attach(self, page: Page) -> None:
        def on_dialog(dialog) -> None:
            self.messages.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", on_dialog)


def _new_page(context):
    page = context.new_page()
    recorder = DialogRecorder()
    recorder.attach(page)
    return page, recorder


# ---------------------------------------------------------------------------
# 1. Salaries tax calculator (IRD stc.htm, three assessment years)
# ---------------------------------------------------------------------------

def drive_salaries_year(context, year_label: str, year_dir: str,
                        income: str, mpf: str,
                        screenshot_prefix: str) -> CaseResult:
    """Drive https://www.ird.gov.hk/eng/ese/st_comp_<year>/stc.htm."""
    url = f"https://www.ird.gov.hk/eng/ese/{year_dir}/stc.htm"
    case = CaseResult(name=f"salaries_{year_label}", url=url, started_at=hkt_now())
    page, dialogs = _new_page(context)
    try:
        case.step(f"goto {url} (route-fulfilled with live official bytes; "
                  "stc bootstrap init-script registered: official savefrmIn() snapshot "
                  "before initPage, restoring frameset-provided frmInStatus)")
        page.goto(url, wait_until="load", timeout=60_000)

        case.step("inject minimal <iframe name=main> shim (frameset result-frame restoration)")
        page.evaluate(_SHIM_FRAME_JS)

        case.step(f"select Single radio, fill employment income={income}, MPF mandatory={mpf}")
        page.check("#SingleSeparatedDivorcedWidowed")
        page.fill("#SelfIncome", income)
        page.fill("#SelfMPF", mpf)
        page.screenshot(path=f"{screenshot_prefix}_input.png", full_page=True)
        case.shot(f"{screenshot_prefix}_input.png")

        case.step("click official Compute button (#ComputeButton, name=B1)")
        page.click("#ComputeButton")

        case.step("wait for official eoutdis.htm result page rendered inside the main frame")
        page.wait_for_function(
            "() => { const t = (" + _IFRAME_TEXT_JS.strip() + ")(); return !!t && t.length > 200; }",
            timeout=30_000,
        )
        page.wait_for_timeout(400)  # let document.write finish painting
        raw = page.evaluate(_IFRAME_TEXT_JS) or ""
        case.official_raw = raw
        page.screenshot(path=f"{screenshot_prefix}_result.png", full_page=True)
        case.shot(f"{screenshot_prefix}_result.png")

        before, after, bare = _salary_rows(raw)
        if before is None and after is None and bare is not None:
            # e.g. 2026/27: no Schedule 43 rebate entry -> the official page shows
            # a single "TAX PAYABLE BY YOU" row without BEFORE/AFTER labels.
            before = after = bare
            case.extra_observations.append(
                "official page displays a single 'TAX PAYABLE BY YOU' row (no reduction rows - "
                "no 2026/27 Schedule 43 entry); before=final=value, reduction=0"
            )
        case.official_value = after or before
        case.extra_observations.append(
            f"parsed from displayed rows: before_reduction={before or 'n/a'}, "
            f"final_after_reduction={after or 'n/a'}"
        )
        if "two-tiered standard rates" in raw:
            case.extra_observations.append("official page shows the '*At two-tiered standard rates' remark -> standard-rate path chosen")
        else:
            case.extra_observations.append("no two-tiered standard-rate remark -> progressive-rate path chosen")
        if dialogs.messages:
            case.extra_observations.append(f"page dialogs: {dialogs.messages!r}")
        case.finish(ok=bool(after or before))
    except Exception as exc:  # noqa: BLE001 - honest failure reporting
        case.finish(ok=False, error=repr(exc))
        try:
            page.screenshot(path=f"{screenshot_prefix}_error.png", full_page=True)
            case.shot(f"{screenshot_prefix}_error.png")
        except Exception:  # noqa: BLE001
            pass
    finally:
        page.close()
    return case


def _salary_rows(text: str) -> tuple[str | None, str | None, str | None]:
    """Parse 'TAX PAYABLE BY YOU' rows from the official eoutdis.htm innerText.

    Returns (before_reduction, final_after_reduction, bare_single_row).
    Years with a Schedule 43 rebate entry render BEFORE/AFTER labelled rows;
    years without one (e.g. 2026/27) render a single unlabelled row.
    """
    before = after = bare = None
    for line in text.splitlines():
        if "TAX PAYABLE BY YOU" not in line:
            continue
        m = re.search(r"([\d,]+)\s*$", line.strip())
        if not m:
            continue
        val = normalize_amount(m.group(1))
        upper = line.upper()
        if "BEFORE" in upper:
            before = val
        elif "AFTER" in upper:
            after = val
        else:
            bare = val
    return before, after, bare


# ---------------------------------------------------------------------------
# 2. GovHK stamp duty on property (AVD) - entry page + linked IRD calculator
# ---------------------------------------------------------------------------

GOVHK_SD_ENTRY = "https://www.gov.hk/en/residents/taxes/etax/services/stamp_duty_computation.htm"
AVD_RESIDENTIAL_URL = "https://www.ird.gov.hk/eng/ese/sd_comp/sdpt_s2_2026.htm"


def drive_govhk_entry(context, screenshot_prefix: str) -> CaseResult:
    """Open the GovHK entry page and click through to the residential AVD calculator."""
    case = CaseResult(name="govhk_sd_entry", url=GOVHK_SD_ENTRY, started_at=hkt_now())
    page, _rec = _new_page(context)
    try:
        case.step(f"goto {GOVHK_SD_ENTRY} (official GovHK entry page; domcontentloaded + 1 retry)")
        try:
            page.goto(GOVHK_SD_ENTRY, wait_until="domcontentloaded", timeout=90_000)
        except Exception:  # noqa: BLE001 - heavy portal page; retry once
            case.step("goto timed out; retrying once")
            page.goto(GOVHK_SD_ENTRY, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(800)  # let the link anchors settle
        page.screenshot(path=f"{screenshot_prefix}_entry.png", full_page=True)
        case.shot(f"{screenshot_prefix}_entry.png")

        link = page.locator("a", has_text="Ad Valorem Stamp Duty (AVD) (Part 1 of Scale 1 and Scale 2)")
        case.step("click the linked calculator: 'AVD (Part 1 of Scale 1 and Scale 2) - residential property'")
        pages_before = set(context.pages)
        shot_page = page
        try:
            with page.expect_navigation(wait_until="load", timeout=20_000):
                link.first.click()
            landed = page.url
        except Exception:  # noqa: BLE001 - the link may open a popup tab instead
            popups = [pg for pg in context.pages if pg not in pages_before]
            if not popups:
                raise
            shot_page = popups[-1]
            try:
                shot_page.wait_for_load_state("load", timeout=30_000)
            except Exception:  # noqa: BLE001
                shot_page.wait_for_load_state("domcontentloaded", timeout=30_000)
            case.extra_observations.append("link opened the calculator in a new tab (popup)")
            landed = shot_page.url
        case.extra_observations.append(f"landed on: {landed}")
        if landed.rstrip("/") != AVD_RESIDENTIAL_URL.rstrip("/"):
            case.step(f"WARNING: landed URL differs from expected {AVD_RESIDENTIAL_URL}")
        shot_page.screenshot(path=f"{screenshot_prefix}_calculator.png", full_page=True)
        case.shot(f"{screenshot_prefix}_calculator.png")
        if shot_page is not page:
            shot_page.close()
        case.finish(ok=True)
    except Exception as exc:  # noqa: BLE001
        case.finish(ok=False, error=repr(exc))
    finally:
        page.close()
    return case


def drive_avd_case(context, case_id: str, consideration: str,
                   screenshot_prefix: str) -> CaseResult:
    """Drive the residential AVD calculator (sdpt_s2_2026.htm) for one amount."""
    url = AVD_RESIDENTIAL_URL
    case = CaseResult(name=case_id, url=url, started_at=hkt_now())
    page, dialogs = _new_page(context)
    try:
        case.step(f"goto {url} (calculator linked from the GovHK entry page)")
        page.goto(url, wait_until="load", timeout=60_000)
        case.step(f"fill 'Property price or value' = {consideration}")
        page.fill("#Property_price", consideration)
        page.screenshot(path=f"{screenshot_prefix}_input.png", full_page=True)
        case.shot(f"{screenshot_prefix}_input.png")

        case.step("click official Compute button (#button_compute, name=B1)")
        page.click("#button_compute")
        page.wait_for_timeout(250)
        value = page.input_value("#Stamp_Duty_Payable")
        case.official_raw = f"Stamp Duty Payable input[name=T2] displayed value: {value}"
        case.official_value = normalize_amount(value)
        page.screenshot(path=f"{screenshot_prefix}_result.png", full_page=True)
        case.shot(f"{screenshot_prefix}_result.png")
        if dialogs.messages:
            case.extra_observations.append(f"page dialogs: {dialogs.messages!r}")
        case.finish(ok=bool(case.official_value))
    except Exception as exc:  # noqa: BLE001
        case.finish(ok=False, error=repr(exc))
        try:
            page.screenshot(path=f"{screenshot_prefix}_error.png", full_page=True)
            case.shot(f"{screenshot_prefix}_error.png")
        except Exception:  # noqa: BLE001
            pass
    finally:
        page.close()
    return case


# ---------------------------------------------------------------------------
# 3. IRD lease stamp duty calculator (csdta.htm)
# ---------------------------------------------------------------------------

LEASE_URL = "https://www.ird.gov.hk/chi/ese/sd_comp/csdta.htm"


def drive_lease_case(context, total_rent: str, monthly_equiv: str,
                     start: tuple[str, str, str], end: tuple[str, str, str],
                     screenshot_prefix: str) -> CaseResult:
    """Drive the lease calculator: certain 6-month term, total-rent input.

    The official page itself refuses monthly-rent input for terms <= 1 year
    (recorded as an extra observation), so the equivalent total rent is entered.
    """
    url = LEASE_URL
    case = CaseResult(name="lease_6months", url=url, started_at=hkt_now())
    page, dialogs = _new_page(context)
    try:
        case.step(f"goto {url} (official IRD lease calculator; Chinese UI, utf-8)")
        page.goto(url, wait_until="load", timeout=60_000)

        case.step(
            "first demonstrate the official convention: monthly-rent input for a "
            f"<=1 year term is rejected by the page (monthly equivalent {monthly_equiv})"
        )
        page.check('input[name="D1"] >> nth=0')  # Certain Lease Term
        page.fill('input[name="T1_D"]', start[0])
        page.fill('input[name="T1_M"]', start[1])
        page.fill('input[name="T1_Y"]', start[2])
        page.fill('input[name="T2_D"]', end[0])
        page.fill('input[name="T2_M"]', end[1])
        page.fill('input[name="T2_Y"]', end[2])
        page.check('input[name="D2"] >> nth=0')  # Monthly Rent radio
        page.fill('input[name="T3"]', monthly_equiv)
        page.click("#button_compute")
        page.wait_for_timeout(300)
        if dialogs.messages:
            case.extra_observations.append(
                "official monthly-rent rejection dialog (<=1 year term): "
                + " | ".join(dialogs.messages)
            )
        else:
            case.extra_observations.append("monthly-rent attempt produced no dialog (unexpected)")
        dialogs.messages.clear()

        case.step(
            f"drive the accepted path: Total Rent over whole term = {total_rent} "
            f"(= monthly {monthly_equiv} x 6 months)"
        )
        page.check('input[name="D2"] >> nth=1')  # Total rent radio
        page.fill('input[name="T4"]', total_rent)
        page.screenshot(path=f"{screenshot_prefix}_input.png", full_page=True)
        case.shot(f"{screenshot_prefix}_input.png")
        page.click("#button_compute")
        page.wait_for_timeout(300)
        value = page.input_value('input[name="T7"]')
        case.official_raw = f"Current Stamp Duty Payable input[name=T7] displayed value: {value}"
        case.official_value = normalize_amount(value)
        page.screenshot(path=f"{screenshot_prefix}_result.png", full_page=True)
        case.shot(f"{screenshot_prefix}_result.png")
        case.finish(ok=bool(case.official_value))
    except Exception as exc:  # noqa: BLE001
        case.finish(ok=False, error=repr(exc))
        try:
            page.screenshot(path=f"{screenshot_prefix}_error.png", full_page=True)
            case.shot(f"{screenshot_prefix}_error.png")
        except Exception:  # noqa: BLE001
            pass
    finally:
        page.close()
    return case
