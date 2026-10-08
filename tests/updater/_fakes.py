"""tests/updater 共享假件与测试宇宙（M5 Red；Annex C C1 假件契约）。

本模块为测试基建：进程内假时钟／假出站／假 harness，不新增第三方库；
不在模块级导入 app.*（各测试体内延迟导入 app.updater.*，使 Red 阶段每个
测试的失败原因＝「缺失的 app.updater 模块」，而非收集期整体失败）。

测试宇宙（【拟名】数据契约，Green 阶段须按此实现解析/分类/调度）:
  - 来源 A ird_budget（html_table）: 唯一 auto slot＝
    salaries.reductions.2025_26.cap；当前值 "3000"（与
    app.rules.bundle.INITIAL_BUNDLE_CONTENT 一致 → 无变更检查）。
  - 来源 B ird_sdo_index（html_index 发现源）: 无规则字段（C7.5）。
  - 来源 C iro_sch43（html_table 独立对应来源）: 同 fullpath 的独立证据。
  - anchor path_regex 语法（C7.3 有界定位）: "heading:<标题文本>" 按序拼接
    "#row:<行标>"／"#col:<列标>"；值提取仅来自锚定 DOM 表格单元格，
    非正文模糊抽取。
  - legal_state_rules（manifest 内数据驱动）: 规则列表
    [{anchor, match, state, period}]——锚定节文本含 match 子串 → 进入
    state（C7.9 枚举），作用于 period（课税年度键）。
  - 快照 hash 去重（C7.8）: 同 URL 同 content_hash 重复抓取不重复入档；
    抓取字节与最近已核验快照一致 → 无变更。
"""

from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Asia/Hong_Kong＝固定 +08:00（1979 起无夏令时；测试期望与 ZoneInfo 实现等价）
HK = timezone(timedelta(hours=8))

BUDGET_URL = "https://www.ird.gov.hk/eng/tax/budget.htm"
SDO_INDEX_URL = "https://www.ird.gov.hk/eng/ppr/sdo.htm"
SCH43_URL = "https://www.elegislation.gov.hk/hk/cap112!schedule43"

# 模型端点（更新路径绝不可调用；REQ-17「不依赖模型」）
MODEL_ENDPOINTS = ("https://model.invalid/v1/chat/completions",)

# 财务会话字段名（更新出站不得携带；REQ-17「不发送任何用户财务数据」）
FINANCIAL_KEYS = (
    "employment_income",
    "mpf_mandatory_contributions",
    "married_status",
    "dependent_children",
    "spouse_employment_income",
    "amounts",
    "balance",
    "provisional_paid",
    "next_provisional",
    "final_after_reduction",
)


def hk(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    """测试期望时间（Asia/Hong_Kong）。"""
    return datetime(year, month, day, hour, minute, tzinfo=HK)


# ---------------------------------------------------------------------------
# 假件（Annex C C1：进程内实现、不增第三方库；C6.4 时钟可注入）
# ---------------------------------------------------------------------------
class FakeClock:
    """可注入假时钟：调度器经 clock.now() 取当前时刻（tz-aware，HK）。"""

    def __init__(self, start: datetime) -> None:
        self._now = start.astimezone(HK)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = moment.astimezone(HK)

    def advance(self, *, days: int = 0, minutes: int = 0) -> None:
        self._now = self._now + timedelta(days=days, minutes=minutes)


class FakeOutbound:
    """进程内假出站：更新器一切出站经 outbound.fetch(url, **kwargs) -> bytes。

    - documents: url -> bytes（正常文档）或 BaseException（该源脚本化失败）；
    - unreachable=True 或未知 URL → ConnectionError（网络不可达）；
    - hold: threading.Event——设置后每次 fetch 阻塞至 set()（singleflight 测试）；
    - requests: 全部调用记录 [{"url", "kwargs"}]（no-financial-data 断言用）。
    """

    def __init__(self, documents: dict[str, Any] | None = None) -> None:
        self.documents: dict[str, Any] = dict(documents or {})
        self.requests: list[dict] = []
        self.unreachable = False
        self.hold: threading.Event | None = None

    def fetch(self, url: str, **kwargs: Any) -> bytes:
        self.requests.append({"url": url, "kwargs": kwargs})
        if self.hold is not None:
            self.hold.wait()
        if self.unreachable or url not in self.documents:
            raise ConnectionError(f"网络不可达（假出站）：{url}")
        document = self.documents[url]
        if isinstance(document, BaseException):
            raise document
        return document


class StrictOutbound(FakeOutbound):
    """门禁隔离假出站：任何出站尝试即 AssertionError（C8.7 无网络）。"""

    def fetch(self, url: str, **kwargs: Any) -> bytes:
        self.requests.append({"url": url, "kwargs": kwargs})
        raise AssertionError(f"门禁运行环境不得访问网络（C8.7）：{url}")


class FakeHarness:
    """独立参考 harness 假件（C8.1）：run_case(case_id) → 参考期望。

    返回 {"case_id", "stages", "output", "parameter_evidence_ref"}；
    parameter_evidence_ref＝该 case 参数证据引用的独立事实 id（None＝无）。
    未收录的 case 抛 KeyError（＝必需 case 未执行）。
    """

    def __init__(self, results: dict[str, dict]) -> None:
        self.results = dict(results)
        self.calls: list[str] = []

    def run_case(self, case_id: str) -> dict:
        self.calls.append(case_id)
        if case_id not in self.results:
            raise KeyError(f"未知必需 case：{case_id}")
        return self.results[case_id]


class FakeEngine:
    """候选引擎假件：run_case(case_id, candidate_content) → 候选实际输出。

    可脚本为与 FakeHarness 输出一致（模拟「双引擎一致」）或不一致。
    """

    def __init__(self, results: dict[str, dict]) -> None:
        self.results = dict(results)
        self.calls: list[str] = []

    def run_case(self, case_id: str, candidate_content: dict) -> dict:
        self.calls.append(case_id)
        if case_id not in self.results:
            raise KeyError(f"未知必需 case：{case_id}")
        return self.results[case_id]


# ---------------------------------------------------------------------------
# 测试宇宙：manifest（C7.2 封闭键集合）＋ 文档
# ---------------------------------------------------------------------------
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

SCH43_MANIFEST: dict = {
    "manifest_schema_version": "1",
    "source_id": "iro_sch43",
    "url": SCH43_URL,
    "artifact_type": "html_table",
    "parser_id": "iro_sch43_table_v1",
    "parser_code_hash": "sha256:" + "cd" * 32,
    "anchors": [
        {
            "path_regex": "heading:Schedule 43 - Salaries Tax Reduction",
            "anchor_status": "confirmed",
        },
        {
            "path_regex": (
                "heading:Schedule 43 - Salaries Tax Reduction#row:2025/26#col:Cap"
            ),
            "anchor_status": "confirmed",
        },
    ],
    "fields": ["reduction_cap_2025_26_law"],
    "slots": [
        {
            "profile_ID": "salaries_reduction_v1",
            "fullpath": "salaries.reductions.2025_26.cap",
            "type": "decimal_string",
            "unit": "HKD",
            "constraints": {"min": "0", "max": "1000000", "step": "1"},
            "anchor": (
                "heading:Schedule 43 - Salaries Tax Reduction#row:2025/26#col:Cap"
            ),
            "proof_role": "independent_counterpart",
        }
    ],
    "evidence_only_fields": [],
    "legal_state_rules": [],
    "proof_authority": "elegislation.gov.hk 法例附表文本（heading＋行/列标锚定）",
    "independent_counterpart": "ird_budget 官方页面（互为独立对应）",
}

SDO_INDEX_MANIFEST: dict = {
    "manifest_schema_version": "1",
    "source_id": "ird_sdo_index",
    "url": SDO_INDEX_URL,
    "artifact_type": "html_index",
    "parser_id": "ird_sdo_index_v1",
    "parser_code_hash": "sha256:" + "ef" * 32,
    "anchors": [
        {
            "path_regex": "heading:Stamp Duty Ordinance gazette index",
            "anchor_status": "confirmed",
        }
    ],
    "fields": [],
    "slots": [],
    "evidence_only_fields": ["index_links"],
    "legal_state_rules": [],
    "proof_authority": "ird.gov.hk 修订索引页（heading 锚定）",
    "independent_counterpart": "—（发现源，无规则字段）",
}

BUDGET_DOC_OK = (
    b"<html><body>"
    b"<h2>Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Reduction</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>100%</td><td>3000</td></tr>"
    b"</table>"
    b"</body></html>"
)

BUDGET_DOC_CHANGED = (
    b"<html><body>"
    b"<h2>Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Reduction</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>100%</td><td>3500</td></tr>"
    b"</table>"
    b"</body></html>"
)

# 值表不变＋锚定通知（legal_state_rules 输入）；三份文档各自只含一种措辞。
BUDGET_DOC_UNCERTAIN_NOTICE = (
    BUDGET_DOC_OK[:-len(b"</body></html>")]
    + b"<h2>Legislative Update</h2>"
    + b"<p>The reduction cap has been amended. The effective year of assessment"
    b" will be confirmed.</p>"
    + b"</body></html>"
)

BUDGET_DOC_PROPOSAL_NOTICE = (
    BUDGET_DOC_OK[: -len(b"</body></html>")]
    + b"<h2>Legislative Update</h2>"
    + b"<p>The Government proposes to raise the cap upon enactment.</p>"
    + b"</body></html>"
)

BUDGET_DOC_EXPIRED_NOTICE = (
    BUDGET_DOC_OK[: -len(b"</body></html>")]
    + b"<h2>Legislative Update</h2>"
    + b"<p>The reduction is repealed with effect from 2026-04-01.</p>"
    + b"</body></html>"
)

SCH43_DOC_3000 = (
    b"<html><body>"
    b"<h2>Schedule 43 - Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>3000</td></tr>"
    b"</table>"
    b"</body></html>"
)

SCH43_DOC_3500 = (
    b"<html><body>"
    b"<h2>Schedule 43 - Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>3500</td></tr>"
    b"</table>"
    b"</body></html>"
)

SDO_INDEX_DOC = (
    b"<html><body>"
    b"<h2>Stamp Duty Ordinance gazette index</h2>"
    b"<ul><li><a href=\"/eng/pdf/sdo/gazette/es1202400010123.pdf\">"
    b"es1202400010123.pdf</a></li></ul>"
    b"</body></html>"
)


def base_sources() -> list[dict]:
    """两源 allowlist（预算页＋发现索引页）。"""
    return [
        {"source_id": "ird_budget", "url": BUDGET_URL, "manifest": copy.deepcopy(BUDGET_MANIFEST)},
        {"source_id": "ird_sdo_index", "url": SDO_INDEX_URL, "manifest": copy.deepcopy(SDO_INDEX_MANIFEST)},
    ]


def sources_with_law() -> list[dict]:
    """三源 allowlist：另含独立对应法例来源 iro_sch43（C8.1 独立证据）。"""
    return base_sources() + [
        {"source_id": "iro_sch43", "url": SCH43_URL, "manifest": copy.deepcopy(SCH43_MANIFEST)},
    ]


def base_documents() -> dict[str, bytes]:
    """与现行 bundle 一致的「无变更」文档集。"""
    return {
        BUDGET_URL: BUDGET_DOC_OK,
        SDO_INDEX_URL: SDO_INDEX_DOC,
    }


CHANGE_CAP_3500 = {
    "fullpath": "salaries.reductions.2025_26.cap",
    "value": "3500",
    "source_id": "ird_budget",
    "anchor": "heading:Salaries Tax Reduction#row:2025/26#col:Cap",
}

CHANGE_FROZEN_BANDS = {
    "fullpath": "salaries.progressive_bands",
    "value": [{"width": "50000", "rate_bp": 200}, {"rate_bp": 1700}],
    "source_id": "ird_budget",
    "anchor": "heading:Salaries Tax Reduction",
}

FACT_CAP_3500 = {
    "fact_id": "fact_sch43_cap_3500",
    "fullpath": "salaries.reductions.2025_26.cap",
    "value": "3500",
    "source_id": "iro_sch43",
    "url": SCH43_URL,
    "anchor": "heading:Schedule 43 - Salaries Tax Reduction#row:2025/26#col:Cap",
    "tier": "T1",
}

GATE_CASE = "case_salaries_reduction_cap_2025_26"


# ---------------------------------------------------------------------------
# 编排助手（延迟导入被测/既有模块）
# ---------------------------------------------------------------------------
def seed_published_store(tmp_path: Path, name: str = "rules.db"):
    """临时 DB＋发布 INITIAL_BUNDLE_CONTENT 作为「现行已发布版本」。"""
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT
    from app.rules.store import RuleStore

    store = RuleStore(tmp_path / name)
    store.publish(copy.deepcopy(INITIAL_BUNDLE_CONTENT), evidence_refs=[])
    return store


def make_scheduler(store, *, clock, outbound, sources=None):
    """构造被测调度器（Red 失败原因＝缺失 app.updater.scheduler）。"""
    from app.updater.scheduler import Scheduler

    return Scheduler(
        store,
        clock=clock,
        outbound=outbound,
        sources=base_sources() if sources is None else sources,
    )


def initial_content() -> dict:
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return copy.deepcopy(INITIAL_BUNDLE_CONTENT)


def changed_bundle_content(value: str) -> dict:
    """仅改 salaries.reductions.2025_26.cap 的候选 content（DATA-only 差异）。"""
    content = initial_content()
    content["data"]["salaries"]["reductions"]["2025_26"]["cap"] = value
    return content
