"""验收缺口 Red 测试共享基建（终验 REJECTED 后 Red 阶段；本目录自包含，不跨目录导入）。

内容：
- 回环 CSRF-aware API 封装（契约同 tests/conftest.Api：安全 GET 发放
  X-CSRF-Token，不安全方法回呈＋Origin 与 Host 完全一致，C5.2/C5.3）。
- 进程内 app 工厂把手（app.state.store／app.state.update_scheduler 服务端
  接缝，同 tests/ui/_helpers.py 注入模式）。
- 六个已批准税项的最小合法事实集（字段名＝Annex C C3.5＋Annex D D 系列事实
  模型；构造与 tests/engines/* 既绿测试同形——本模块独立可运行，避免与
  tests/ui/_helpers.py 等既有下划线模块名冲突，故名 _acc_helpers）。
- prepare→confirm→execute 三段驱动（C3.2）。
- 更新调度假件（FakeClock/FakeOutbound）＋预算来源 manifest/文档（驱动
  SOURCE 状态 enter，C7.9；与 tests/updater/_fakes.py 同形测试宇宙）。
- 发布/门禁本地宇宙（通过报告/独立事实/严格出站/候选内容差）。

期望值来源: 全部为结构/状态契约；无金额数值期望。
"""

from __future__ import annotations

import copy
import html as _html
import httpx
from datetime import datetime, timedelta, timezone

_html_escape = _html.escape

BASE_ORIGIN = "http://127.0.0.1:8000"

# Asia/Hong_Kong＝固定 +08:00（同 tests/updater/_fakes 约定）
HK = timezone(timedelta(hours=8))


def hk(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=HK)


# ---------------------------------------------------------------------------
# 回环 API 封装（CSRF/Origin；C5.2/C5.3）
# ---------------------------------------------------------------------------
class AccApi:
    """在合法回环 Host/Origin 下带 CSRF 能力访问 API 的薄封装。"""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._csrf_token: str | None = None

    def _token(self) -> str:
        if self._csrf_token is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, "安全 GET 未发放 X-CSRF-Token（C5.2/C5.3）"
            self._csrf_token = token
        return self._csrf_token

    def get(self, path: str, *, headers: dict | None = None):
        return self._client.get(path, headers=headers)

    def post(self, path: str, *, json: object | None = None, headers: dict | None = None):
        h = dict(headers or {})
        h["Origin"] = BASE_ORIGIN
        h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json, headers=h)


class AccHarness:
    """app 把手＋回环 API 封装（进程内；服务端接缝注入用）。"""

    def __init__(self, app, api: AccApi) -> None:
        self.app = app
        self.api = api

    @property
    def store(self):
        return self.app.state.store

    def close(self) -> None:
        self.api._client.close()


def build_app(setenv, db_path) -> AccHarness:
    """构建进程内应用把手；setenv＝conftest 注入的 monkeypatch.setenv
    （HKTAX_DB_PATH 每应用唯一临时路径，测试结束自动复原）。"""
    setenv("HKTAX_DB_PATH", str(db_path))
    from app.main import create_app  # 延迟导入（同 tests/conftest 约定）

    app = create_app()
    client = httpx.Client(
        transport=httpx.ASGITransport(app=app), base_url=BASE_ORIGIN
    )
    return AccHarness(app, AccApi(client))


# ---------------------------------------------------------------------------
# 六个已批准税项：最小合法事实集＋执行目标（Annex C C3.1/C3.5）
# ---------------------------------------------------------------------------
TAX_EXECUTE_TARGETS: dict[str, str] = {
    "salaries_tax": "/api/v1/calc/salaries-tax",
    "profits_tax": "/api/v1/calc/profits-tax",
    "property_tax": "/api/v1/calc/property-tax",
    "personal_assessment": "/api/v1/personal-assessment/compare",
    "stamp_property": "/api/v1/calc/stamp-duty/property",
    "stamp_stock": "/api/v1/calc/stamp-duty/stock",
    "stamp_lease": "/api/v1/calc/stamp-duty/lease",
}


def _paid_rates_zero() -> dict:
    """差饷两条件显式为零（s.5(1A)：amount "0"＋同意且已缴均真）。"""
    return {
        "amount": "0",
        "agreed": {"value": True},
        "actually_paid": {"value": True},
    }


def salaries_min_facts(*, year: str = "2025_26", with_provisional_zero: bool = False) -> dict:
    """单身受雇人士最小事实集（PAM39 Q1 载荷事实，仅作输入不作税额期望）。"""
    facts: dict = {
        "year_of_assessment": year,
        "employment_income": "240000",
        "mpf_mandatory_contributions": "9000",
        "married_status": {"value": "single"},
    }
    if with_provisional_zero:
        facts["provisional_paid"] = "0"
    return facts


def profits_min_facts() -> dict:
    """独资非法团最小事实集（两级制资格三项全部显式确认）。"""
    return {
        "year_of_assessment": "2025_26",
        "entity_kind": "sole_proprietorship",
        "assessable_profit": "600000",
        "two_tier": {
            "connected_entities": [],
            "election_made": {"value": True},
            "no_other_election_same_year": {"value": True},
        },
    }


def property_min_facts() -> dict:
    """单一物业最小事实集（pty.htm Q7 载荷事实：租金 120,000、差饷 0）。"""
    return {
        "year_of_assessment": "2025_26",
        "properties": [
            {
                "property_id": "p-1",
                "rent_received": "120000",
                "rates_paid_by_owner": _paid_rates_zero(),
                "irrecoverable_rent": "0",
                "deposit_offsets": "0",
            }
        ],
    }


def personal_assessment_min_facts() -> dict:
    """Q32 载荷事实（已婚两人：本人薪金＋配偶物业；整体结构保留）。"""
    return {
        "year_of_assessment": "2025_26",
        "married_status": {"value": "married"},
        "persons": [
            {
                "person_id": "self",
                "employment_income": "250000",
                "mpf_mandatory_contributions": "0",
                "properties": [],
                "businesses": [],
                "dependent_parents": [],
                "dependent_children": [],
            },
            {
                "person_id": "spouse",
                "employment_income": "0",
                "mpf_mandatory_contributions": "0",
                "properties": [
                    {
                        "property_id": "p-1",
                        "share": "1/1",
                        "rent_received": "240000",
                        "rates_paid_by_owner": _paid_rates_zero(),
                        "irrecoverable_rent": "0",
                        "deposit_offsets": "0",
                        "mortgage_interest": "0",
                    }
                ],
                "businesses": [],
                "dependent_parents": [],
                "dependent_children": [],
            },
        ],
        "provisional_paid": "0",
    }


def stamp_property_min_facts() -> dict:
    """住宅 AVD 普通买卖协议（Table 9 窗口内 600 万）最小事实集。"""
    return {
        "instrument_date": "2025-06-20",
        "instrument_kind": "agreement_for_sale",
        "property_class": "residential",
        "consideration": "6000000",
        "value": "6000000",
    }


def stamp_stock_min_facts() -> dict:
    """股票普通买卖 contract note（sold 一份）最小事实集。"""
    return {
        "instrument_date": "2025-06-10",
        "complete": {"value": True},
        "documents": [
            {
                "doc_kind": "contract_note_sold",
                "consideration": "50000",
                "value": "50000",
            }
        ],
    }


def stamp_lease_min_facts() -> dict:
    """两年固定租约（月租 10,000、premium 显式 "0"）最小事实集。"""
    return {
        "instrument_date": "2025-06-20",
        "term_kind": "fixed",
        "term_start": "2025-07-01",
        "term_end": "2027-06-30",
        "rent_input_mode": "fixed_monthly",
        "monthly_rent": "10000",
        "premium": "0",
    }


def minimal_facts(tax_type: str) -> dict:
    builders = {
        "salaries_tax": lambda: salaries_min_facts(with_provisional_zero=True),
        "profits_tax": profits_min_facts,
        "property_tax": property_min_facts,
        "personal_assessment": personal_assessment_min_facts,
        "stamp_property": stamp_property_min_facts,
        "stamp_stock": stamp_stock_min_facts,
        "stamp_lease": stamp_lease_min_facts,
    }
    return builders[tax_type]()


# ---------------------------------------------------------------------------
# prepare→confirm→execute 三段驱动（C3.2）
# ---------------------------------------------------------------------------
PREPARE_PATH = "/api/v1/calc/prepare"
CONFIRM_PATH = "/api/v1/calc/confirm"


def prepare(api, tax_type: str, facts: dict):
    return api.post(
        PREPARE_PATH,
        json={"tax_type": tax_type, "schema_version": "1.0.0", "input": facts},
    )


def confirm(api, prepared: dict):
    return api.post(
        CONFIRM_PATH,
        json={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": True,
        },
    )


def execute(api, confirmation_id: str, target: str):
    return api.post(target, json={"confirmation_id": confirmation_id})


def error_code(response) -> str | None:
    """统一错误形状 §4 的 error.code（缺失 → None）。"""
    try:
        err = response.json().get("error")
    except Exception:
        return None
    return err.get("code") if isinstance(err, dict) else None


# ---------------------------------------------------------------------------
# 更新调度假件（C6.4 时钟／出站；与 tests/updater/_fakes 同形）
# ---------------------------------------------------------------------------
class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start.astimezone(HK)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = moment.astimezone(HK)


class FakeOutbound:
    """进程内假出站：fetch(url) -> bytes；unreachable/未知 URL → ConnectionError；
    requests 记录全部调用（隔离断言用）。"""

    def __init__(self, documents: dict | None = None) -> None:
        self.documents: dict = dict(documents or {})
        self.requests: list[dict] = []
        self.unreachable = False

    def fetch(self, url: str, **kwargs) -> bytes:
        self.requests.append({"url": url, "kwargs": kwargs})
        if self.unreachable or url not in self.documents:
            raise ConnectionError(f"网络不可达（假出站）：{url}")
        return self.documents[url]


class StrictOutbound(FakeOutbound):
    """门禁隔离假出站：任何出站尝试即 AssertionError（C8.7 无网络）。"""

    def fetch(self, url: str, **kwargs) -> bytes:
        self.requests.append({"url": url, "kwargs": kwargs})
        raise AssertionError(f"门禁运行环境不得访问网络（C8.7）：{url}")


class FakeHarness:
    """独立参考 harness 假件（C8.1 seam）。"""

    def __init__(self, results: dict[str, dict]) -> None:
        self.results = dict(results)

    def run_case(self, case_id: str) -> dict:
        if case_id not in self.results:
            raise KeyError(f"未知必需 case：{case_id}")
        return self.results[case_id]


class FakeEngine:
    """候选引擎假件（与 harness 输出一致的对照用）。"""

    def __init__(self, results: dict[str, dict]) -> None:
        self.results = dict(results)

    def run_case(self, case_id: str, candidate_content: dict) -> dict:
        if case_id not in self.results:
            raise KeyError(f"未知必需 case：{case_id}")
        return self.results[case_id]


BUDGET_URL = "https://www.ird.gov.hk/eng/tax/budget.htm"

# 预算页 manifest（C7.2 封闭键集合；结构同 tests/updater/_fakes 同形宇宙）
BUDGET_MANIFEST: dict = {
    "manifest_schema_version": "1",
    "source_id": "ird_budget",
    "url": BUDGET_URL,
    "artifact_type": "html_table",
    "parser_id": "ird_budget_table_v1",
    "parser_code_hash": "sha256:" + "ab" * 32,
    "anchors": [
        {"path_regex": "heading:Salaries Tax Reduction", "anchor_status": "confirmed"},
        {
            "path_regex": "heading:Salaries Tax Reduction#row:2025/26#col:Cap",
            "anchor_status": "confirmed",
        },
        {"path_regex": "heading:Legislative Update", "anchor_status": "confirmed"},
    ],
    "fields": ["reduction_cap_2025_26"],
    "slots": [
        {
            "profile_ID": "salaries_reduction_v1",
            "fullpath": "salaries.reductions.2025_26.cap",
            "type": "decimal_string",
            "unit": "HKD",
            "constraints": {"min": "0", "max": "1000000", "step": "1"},
            "anchor": "heading:Salaries Tax Reduction#row:2025/26#col:Cap",
            "proof_role": "primary_official_page",
        }
    ],
    "evidence_only_fields": ["reduction_subject_scope", "provisional_boundary"],
    "legal_state_rules": [
        {
            "anchor": "heading:Legislative Update",
            "match": "will be confirmed",
            "state": "related_change_uncertain",
            "period": "2026_27",
        },
        {
            "anchor": "heading:Legislative Update",
            "match": "proposes",
            "state": "proposal_not_current",
            "period": "2026_27",
        },
        {
            "anchor": "heading:Legislative Update",
            "match": "repealed",
            "state": "expired",
            "period": "2024_25",
        },
    ],
    "proof_authority": "ird.gov.hk 官方页面原文（heading＋行/列标锚定）",
    "independent_counterpart": "iro_sch43 法例附表文本（独立对应来源）",
}

# 值表与现行 bundle 一致（无变更）＋无状态通知
BUDGET_DOC_OK = (
    b"<html><body>"
    b"<h2>Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Reduction</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>100%</td><td>3000</td></tr>"
    b"</table>"
    b"</body></html>"
)

# 值表不变＋锚定通知「repealed」→ legal_state_rules 判 expired（2024_25）
BUDGET_DOC_EXPIRED_NOTICE = (
    b"<html><body>"
    b"<h2>Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Reduction</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>100%</td><td>3000</td></tr>"
    b"</table>"
    b"<h2>Legislative Update</h2>"
    b"<p>The reduction is repealed with effect from 2026-04-01.</p>"
    b"</body></html>"
)


def budget_source() -> dict:
    """单源 allowlist（预算页；驱动 SOURCE 状态 enter 用）。"""
    return {
        "source_id": "ird_budget",
        "url": BUDGET_URL,
        "manifest": copy.deepcopy(BUDGET_MANIFEST),
    }


def make_scheduler(store, *, clock, outbound, sources=None):
    from app.updater.scheduler import Scheduler

    return Scheduler(
        store,
        clock=clock,
        outbound=outbound,
        sources=[budget_source()] if sources is None else sources,
    )


# ---------------------------------------------------------------------------
# 发布/门禁本地宇宙（REQ-18；结构同 tests/updater 既有口径）
# ---------------------------------------------------------------------------
REPORT_PUBLISH_OK = {
    "decision": "publish",
    "tests": {"passed": 3, "failed": 0, "failed_ids": []},
    "quarantine_reason": None,
}

GATE_CASE = "case_salaries_reduction_cap_2025_26"

FACT_CAP_3500 = {
    "fact_id": "fact_sch43_cap_3500",
    "fullpath": "salaries.reductions.2025_26.cap",
    "value": "3500",
    "source_id": "iro_sch43",
    "url": "https://www.elegislation.gov.hk/hk/cap112!schedule43",
    "anchor": "heading:Schedule 43 - Salaries Tax Reduction#row:2025/26#col:Cap",
    "tier": "T1",
}


def initial_content() -> dict:
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return copy.deepcopy(INITIAL_BUNDLE_CONTENT)


def changed_bundle_content(value: str) -> dict:
    """仅改 salaries.reductions.2025_26.cap 的候选 content（DATA-only 差异）。"""
    content = initial_content()
    content["data"]["salaries"]["reductions"]["2025_26"]["cap"] = value
    return content


# ---------------------------------------------------------------------------
# 第三批 Red 假件扩展（终验第二轮 REJECTED 缺口）
# - 受养父母申索事实（Annex C C3.5 既有字段＋拟名三态扩展，见
#   test_acceptance_dependent_parents.py 头部【拟名】契约）。
# - 生产 Scheduler 装配读取/注入接缝（manifests 与 harness/engine/
#   required_cases 绑定的可观测面；clock/outbound 测试替换点）。
# - 按生产 manifest anchors/slots 渲染「与现行束一致」的官方页快照假件
#   （测试不必预知 Green 将编写的锚点文本——文档由 manifest 自描述渲染，
#   值默认取现行束同 fullpath 值 → 无变更；overrides 可按 fullpath 改写）。
# ---------------------------------------------------------------------------
def parent_entry(
    birth_date: str,
    *,
    relationship: str = "mother",
    residence: object = True,
    maintained: object = True,
    together: object = True,
) -> dict:
    """受养父母申索事实（拟名三态字段：maintained=s.30(4) 供养事实；
    living_together_all_year=s.30(3)(b) 全年同住额外额事实）。"""
    return {
        "relationship": relationship,
        "birth_date": birth_date,
        "residence": {"value": residence},
        "maintained": {"value": maintained},
        "living_together_all_year": {"value": together},
    }


def resolve_bundle_path(bundle_data, fullpath: str):
    """按束内规范点路径取 data 值（允许 data. 前缀；缺失 → None）。"""
    parts = fullpath.split(".")
    if parts and parts[0] == "data":
        parts = parts[1:]
    node = bundle_data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def scheduler_sources(scheduler) -> list:
    """生产 Scheduler 的来源条目（公开名 sources 或下划线名均可）。"""
    for name in ("sources", "_sources"):
        if hasattr(scheduler, name):
            return getattr(scheduler, name)
    raise AttributeError("Scheduler 未暴露来源清单（sources/_sources 接缝）")


def scheduler_binding(scheduler, name: str):
    """读取 Scheduler 绑定（harness／engine／required_cases；公开名优先）。"""
    for candidate in (name, f"_{name}"):
        if hasattr(scheduler, candidate):
            return getattr(scheduler, candidate)
    return None


def inject_scheduler_seams(scheduler, *, clock=None, outbound=None) -> None:
    """替换生产 Scheduler 的 clock/outbound（其余生产装配保持不变）。"""
    for name, value in (("clock", clock), ("outbound", outbound)):
        if value is None:
            continue
        setattr(scheduler, name if hasattr(scheduler, name) else f"_{name}", value)


def _anchor_parts(anchor: str) -> tuple[str, str, str]:
    """C7.3 锚定语法 heading:<H>[#row:R][#col:C] → (heading, row, col)。

    缺省 row/col 用占位标签（锚定缺 col 时 extract 本身 fail closed，
    与渲染无关）。
    """
    heading, row, col = anchor, "Item", "Value"
    for part in anchor.split("#"):
        if part.startswith("heading:"):
            heading = part[len("heading:") :]
        elif part.startswith("row:"):
            row = part[len("row:") :]
        elif part.startswith("col:"):
            col = part[len("col:") :]
    return heading, row, col


def render_source_document(manifest: dict, bundle_data: dict, overrides: dict | None = None) -> bytes:
    """按 manifest 渲染官方页快照假件（与现行束一致；可按 fullpath 改写值）。

    - 同一 heading 的 slots 合并渲染为一个 h2 节（节内逐 slot 一张两列表，
      行标/列标命中 C7.3 锚定单元格）；值默认＝现行束该 fullpath 的值。
    - legal_state_rules 的锚定节渲染为「无通知」占位文本（不触发任何
      match 关键词 → SOURCE 状态不 enter）。
    """
    esc = _html_escape
    sections: dict[str, list[tuple[str, str, str]]] = {}
    order: list[str] = []
    for slot in manifest.get("slots") or []:
        heading, row, col = _anchor_parts(str(slot.get("anchor") or ""))
        value = (overrides or {}).get(slot.get("fullpath"))
        if value is None:
            resolved = resolve_bundle_path(bundle_data, str(slot.get("fullpath") or ""))
            value = resolved if resolved is not None else "__unresolved__"
        if heading not in sections:
            sections[heading] = []
            order.append(heading)
        sections[heading].append((row, col, str(value)))
    parts = ["<html><body>"]
    for heading in order:
        parts.append(f"<h2>{esc(heading)}</h2>")
        for row, col, value in sections[heading]:
            parts.append(
                f"<table><tr><th>{esc(row)}</th><th>{esc(col)}</th></tr>"
                f"<tr><td>{esc(row)}</td><td>{esc(value)}</td></tr></table>"
            )
    rule_headings: list[str] = []
    for rule in manifest.get("legal_state_rules") or []:
        anchor = rule.get("anchor") if isinstance(rule, dict) else None
        if isinstance(anchor, str) and anchor.startswith("heading:"):
            heading = _anchor_parts(anchor)[0]
            if heading not in sections and heading not in rule_headings:
                rule_headings.append(heading)
    for heading in rule_headings:
        parts.append(f"<h2>{esc(heading)}</h2><p>（本节为占位渲染，无通知内容。）</p>")
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


def production_round_documents(sources, bundle_data, overrides_by_source=None) -> dict:
    """全部生产来源 → 渲染快照假件（url → bytes；驱动完整检查轮用）。"""
    documents: dict = {}
    for entry in sources:
        overrides = (overrides_by_source or {}).get(str(entry.get("source_id"))) or {}
        documents[str(entry.get("url"))] = render_source_document(
            entry.get("manifest") or {}, bundle_data, overrides
        )
    return documents
