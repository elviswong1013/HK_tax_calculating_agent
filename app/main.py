"""应用装配与入口（SDD §8：app/main.py；create_app() 为唯一装配点）。

REQ-1/2/9/10/14/17/18 基础面：API 骨架＋C5 安全中间件＋SQLite 规则束存储＋
覆盖数据源＋生产更新调度接线（Annex C C7.5 已知来源 allowlist；首启即查＋
周期 tick 经 lifespan）。
create_app() 同步完成存储初始化（空库则发布 M1 初始束）——进程内 ASGI 测试
（httpx.ASGITransport）不执行 lifespan，故初始化必须在装配期完成；调度检查
（出站）只发生在 lifespan 启动后，测试经 app.state.update_scheduler 接缝注入
假件（tests/ui、tests/acceptance）。
"""

from __future__ import annotations

import copy
import hashlib
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.ai.adapters import HttpxModelOutbound
from app.api.routes import register_routes
from app.config import DEFAULT_PORT, ENGINE_VERSION, db_path_from_env
from app.core.errors import AppError
from app.rules.bundle import INITIAL_BUNDLE_CONTENT
from app.rules.store import RuleStore
from app.updater.fetcher import MAX_SNAPSHOT_BYTES
from app.updater.reference import (
    REQUIRED_CASES,
    CandidateEngine,
    ReferenceHarness,
)
from app.updater.scheduler import Scheduler
from app.web.security import (
    SecurityMiddleware,
    SessionRegistry,
    validate_bind_host,
)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "web" / "templates"

# Annex C C7.5 已知官方 manifest 源（精确 https URL）。第三轮生产接线：逐源
# 补齐非空 anchors（anchor_status=confirmed）与 typed slots（C7.1 七字段）；
# fullpath 一律指向现行已发布束内真实 data 路径，取值为 app/rules/bundles/* 已
# 入数（T1 台账晋升）数值；unit/constraints 具体，slot.anchor 指向本源
# anchors[] 命名空间（C7.2/C7.3）。ird_pam61 为 pdf_layout，待 PDF 解析选型获批
# （C7.3 OPEN gate，用户 2026-10-07 暂不增加依赖）后再入 allowlist——无批准
# 解析器时纳入会使每轮检查 fail closed。
_KNOWN_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("ird_budget", "https://www.ird.gov.hk/eng/tax/budget.htm", "html_section"),
    ("ird_avd", "https://www.ird.gov.hk/eng/faq/avd.htm", "html_table"),
    (
        "govhk_stamp",
        "https://www.gov.hk/en/residents/taxes/stamp/stamp_duty_rates.htm",
        "html_table",
    ),
    ("ird_sdo_index", "https://www.ird.gov.hk/eng/ppr/sdo.htm", "html_index"),
    ("ird_pt", "https://www.ird.gov.hk/eng/faq/pty.htm", "html_section"),
    (
        "ird_pa2026",
        "https://www.ird.gov.hk/eng/faq/policyaddress2026.htm",
        "html_section",
    ),
    (
        "govhk_salaries_allowances",
        "https://www.gov.hk/en/residents/taxes/salaries/allowances/allowances/7years.htm",
        "html_table",
    ),
    ("ird_bus_pft", "https://www.ird.gov.hk/eng/tax/bus_pft.htm", "html_section"),
)

_PARSER_ID = "stdlib_bounded_html_v1"

# 逐源 manifest 内容（C7.2 封闭键集合中除通用装配字段外的部分）。值不预填
# （值来自快照抽取）；anchors/slots 为 C7.3 锚定 locator，行标/列标变化 →
# extract fail closed。
_SOURCE_MANIFEST_SPECS: dict[str, dict[str, Any]] = {
    "ird_budget": {
        "anchors": [
            {
                "path_regex": "heading:Salaries Tax Reduction",
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Salaries Tax Reduction#row:2025/26#col:Cap"
                ),
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Salaries Tax Reduction#row:2024/25#col:Cap"
                ),
                "anchor_status": "confirmed",
            },
        ],
        "slots": [
            {
                "profile_ID": "salaries_reduction_v1",
                "fullpath": "salaries.reductions.2025_26.cap",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000000", "step": "1"},
                "anchor": "heading:Salaries Tax Reduction#row:2025/26#col:Cap",
                "proof_role": "primary_official_page",
            },
            {
                "profile_ID": "salaries_reduction_v1",
                "fullpath": "salaries.reductions.2024_25.cap",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000000", "step": "1"},
                "anchor": "heading:Salaries Tax Reduction#row:2024/25#col:Cap",
                "proof_role": "primary_official_page",
            },
        ],
        "fields": [
            "salaries_reduction_cap_2025_26",
            "salaries_reduction_cap_2024_25",
        ],
        "evidence_only_fields": [
            "reduction_subject_scope",
            "provisional_boundary",
        ],
        "legal_state_rules": [],
        "proof_authority": "ird.gov.hk 预算页原文（heading＋行/列标锚定）",
        "independent_counterpart": "iro_sch43 法例附表文本（附表 43 独立对应）",
    },
    "ird_avd": {
        "anchors": [
            {
                "path_regex": "heading:AVD - Related Conveyance",
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:AVD - Related Conveyance#row:Fixed duty#col:Amount"
                ),
                "anchor_status": "confirmed",
            },
        ],
        "slots": [
            {
                "profile_ID": "avd_related_conveyance_v1",
                "fullpath": "stamps.property.related_conveyance.fixed_duty",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "10000", "step": "1"},
                "anchor": (
                    "heading:AVD - Related Conveyance#row:Fixed duty#col:Amount"
                ),
                "proof_role": "primary_official_page",
            },
        ],
        "fields": ["avd_related_conveyance_fixed_duty"],
        "evidence_only_fields": ["avd_rate_tables"],
        "legal_state_rules": [],
        "proof_authority": "ird.gov.hk AVD FAQ 原文（heading＋行/列标锚定）",
        "independent_counterpart": "Cap.117 s.29D(2)(a) 法例文本（相关转易契）",
    },
    "govhk_stamp": {
        "anchors": [
            {
                "path_regex": "heading:Stamp Duty Rates",
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Stamp Duty Rates"
                    "#row:Stock transfer - other transfers#col:Fixed duty"
                ),
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Stamp Duty Rates"
                    "#row:Lease - rent ceiling unit#col:Amount"
                ),
                "anchor_status": "confirmed",
            },
        ],
        "slots": [
            {
                "profile_ID": "stamp_stock_fixed_v1",
                "fullpath": "stamps.stock.other_transfer_fixed",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000", "step": "1"},
                "anchor": (
                    "heading:Stamp Duty Rates"
                    "#row:Stock transfer - other transfers#col:Fixed duty"
                ),
                "proof_role": "primary_official_page",
            },
            {
                "profile_ID": "stamp_lease_ceiling_v1",
                "fullpath": "stamps.lease.rent_ceiling_unit",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "1", "max": "10000", "step": "1"},
                "anchor": (
                    "heading:Stamp Duty Rates#row:Lease - rent ceiling unit#col:Amount"
                ),
                "proof_role": "primary_official_page",
            },
        ],
        "fields": ["stamp_stock_fixed_duties", "stamp_lease_ceiling_unit"],
        "evidence_only_fields": ["stamp_rate_tiers"],
        "legal_state_rules": [],
        "proof_authority": "gov.hk 印花税税率官方页原文（heading＋行/列标锚定）",
        "independent_counterpart": "Cap.117 head 1/2 法例文本（互为独立对应）",
    },
    "ird_sdo_index": {
        "anchors": [
            {
                "path_regex": "heading:SDO Amendment Index",
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:SDO Amendment Index#row:Head 2 effective from#col:Date"
                ),
                "anchor_status": "confirmed",
            },
        ],
        "slots": [
            {
                "profile_ID": "sdo_effective_dates_v1",
                "fullpath": "stamps.stock.effective_from",
                "type": "date_string",
                "unit": "date",
                "constraints": {"format": "YYYY-MM-DD"},
                "anchor": (
                    "heading:SDO Amendment Index#row:Head 2 effective from#col:Date"
                ),
                "proof_role": "primary_official_page",
            },
        ],
        "fields": ["sdo_head2_effective_from"],
        "evidence_only_fields": ["sdo_index_links"],
        "legal_state_rules": [],
        "proof_authority": "ird.gov.hk SDO 修订索引原文（heading＋行/列标锚定）",
        "independent_counterpart": "Cap.117 head 2 法例文本（互为独立对应）",
    },
    "ird_pt": {
        "anchors": [
            {
                "path_regex": "heading:Property Tax Rates",
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Property Tax Rates#row:Standard rate#col:Rate"
                ),
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Property Tax Rates#row:Repair allowance#col:Ratio"
                ),
                "anchor_status": "confirmed",
            },
        ],
        "slots": [
            {
                "profile_ID": "property_rates_v1",
                "fullpath": "property.years.2025_26.standard_rate",
                "type": "rational_string",
                "unit": "ratio",
                "constraints": {"max_numerator": 100, "max_denominator": 100},
                "anchor": "heading:Property Tax Rates#row:Standard rate#col:Rate",
                "proof_role": "primary_official_page",
            },
            {
                "profile_ID": "property_rates_v1",
                "fullpath": "property.repair_allowance_ratio",
                "type": "rational_string",
                "unit": "ratio",
                "constraints": {"max_numerator": 1, "max_denominator": 5},
                "anchor": (
                    "heading:Property Tax Rates#row:Repair allowance#col:Ratio"
                ),
                "proof_role": "primary_official_page",
            },
        ],
        "fields": [
            "property_standard_rate",
            "property_repair_allowance_ratio",
        ],
        "evidence_only_fields": ["property_nav_scope"],
        "legal_state_rules": [],
        "proof_authority": "ird.gov.hk 物业税 FAQ 原文（heading＋行/列标锚定）",
        "independent_counterpart": "Cap.112 s.5(1A) 法例文本（互为独立对应）",
    },
    "ird_pa2026": {
        "anchors": [
            {
                "path_regex": "heading:Salaries Tax Reduction",
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Salaries Tax Reduction#row:2025/26#col:Cap"
                ),
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Salaries Tax Reduction"
                    "#row:2026/27 basic allowance#col:Amount"
                ),
                "anchor_status": "confirmed",
            },
        ],
        "slots": [
            {
                "profile_ID": "salaries_reduction_v1",
                "fullpath": "salaries.reductions.2025_26.cap",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000000", "step": "1"},
                "anchor": "heading:Salaries Tax Reduction#row:2025/26#col:Cap",
                "proof_role": "independent_counterpart",
            },
            {
                "profile_ID": "salaries_allowances_2026_27_v1",
                "fullpath": "salaries.allowances.2026_27.basic",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000000", "step": "1"},
                "anchor": (
                    "heading:Salaries Tax Reduction"
                    "#row:2026/27 basic allowance#col:Amount"
                ),
                "proof_role": "primary_official_page",
            },
        ],
        "fields": [
            "salaries_reduction_cap_2025_26_restated",
            "salaries_basic_allowance_2026_27",
        ],
        "evidence_only_fields": ["policy_address_scope"],
        "legal_state_rules": [],
        "proof_authority": "ird.gov.hk 财政预算案/施政报告官方页原文（heading＋行/列标锚定）",
        "independent_counterpart": "ird_budget 预算页（2025/26 宽减互为独立对应）",
    },
    "govhk_salaries_allowances": {
        "anchors": [
            {
                "path_regex": "heading:Salaries Tax Allowances",
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Salaries Tax Allowances#row:Basic allowance#col:Amount"
                ),
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Salaries Tax Allowances"
                    "#row:Married person's allowance#col:Amount"
                ),
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Salaries Tax Allowances#row:MPF deduction cap#col:Amount"
                ),
                "anchor_status": "confirmed",
            },
        ],
        "slots": [
            {
                "profile_ID": "salaries_allowances_v1",
                "fullpath": "salaries.allowances.2025_26.basic",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000000", "step": "1"},
                "anchor": (
                    "heading:Salaries Tax Allowances#row:Basic allowance#col:Amount"
                ),
                "proof_role": "primary_official_page",
            },
            {
                "profile_ID": "salaries_allowances_v1",
                "fullpath": "salaries.allowances.2025_26.married",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "2000000", "step": "1"},
                "anchor": (
                    "heading:Salaries Tax Allowances"
                    "#row:Married person's allowance#col:Amount"
                ),
                "proof_role": "primary_official_page",
            },
            {
                "profile_ID": "salaries_mpf_cap_v1",
                "fullpath": "salaries.mpf_deduction_cap",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000000", "step": "1"},
                "anchor": (
                    "heading:Salaries Tax Allowances#row:MPF deduction cap#col:Amount"
                ),
                "proof_role": "primary_official_page",
            },
        ],
        "fields": [
            "salaries_allowances_2025_26",
            "salaries_mpf_deduction_cap",
        ],
        "evidence_only_fields": ["allowance_matrix_scope"],
        "legal_state_rules": [],
        "proof_authority": "gov.hk 免税额官方页原文（heading＋行/列标锚定）",
        "independent_counterpart": "Cap.112 附表 3B/4 法例文本（互为独立对应）",
    },
    "ird_bus_pft": {
        "anchors": [
            {
                "path_regex": "heading:Two-tiered Profits Tax Rates",
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Two-tiered Profits Tax Rates#row:Threshold#col:Amount"
                ),
                "anchor_status": "confirmed",
            },
            {
                "path_regex": (
                    "heading:Two-tiered Profits Tax Rates"
                    "#row:2025/26 reduction cap#col:Amount"
                ),
                "anchor_status": "confirmed",
            },
        ],
        "slots": [
            {
                "profile_ID": "profits_two_tier_v1",
                "fullpath": "profits.two_tier_threshold",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "100000000", "step": "1"},
                "anchor": (
                    "heading:Two-tiered Profits Tax Rates#row:Threshold#col:Amount"
                ),
                "proof_role": "primary_official_page",
            },
            {
                "profile_ID": "profits_reduction_v1",
                "fullpath": "profits.reductions.2025_26.cap",
                "type": "decimal_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000000", "step": "1"},
                "anchor": (
                    "heading:Two-tiered Profits Tax Rates"
                    "#row:2025/26 reduction cap#col:Amount"
                ),
                "proof_role": "primary_official_page",
            },
        ],
        "fields": [
            "profits_two_tier_threshold",
            "profits_reduction_cap_2025_26",
        ],
        "evidence_only_fields": ["two_tier_eligibility"],
        "legal_state_rules": [],
        "proof_authority": "ird.gov.hk 利得税官方页原文（heading＋行/列标锚定）",
        "independent_counterpart": "Cap.112 附表 43／2tr 官方 FAQ（互为独立对应）",
    },
}

_OUTBOUND_TIMEOUT_SECONDS = 30.0  # SDD §5：出站超时 30s
_TICK_INTERVAL_SECONDS = 60.0  # 周期 tick；到期/退避判定由 Scheduler 把关（无忙循环）


def _parser_code_hash() -> str:
    """已装配有界解析器代码 hash（C7.4：primary_direct 自动化定义的组成之一）。"""
    source = (Path(__file__).resolve().parent / "updater" / "parser.py").read_bytes()
    return "sha256:" + hashlib.sha256(source).hexdigest()


def _known_source_entries() -> list[dict[str, Any]]:
    """C7.5 已知源 → 调度器 allowlist 条目（{"source_id", "url", "manifest"}）。

    第三轮生产接线：逐源 typed slots 指向现行束内真实 data 路径、anchors
    confirmed（C7.1/C7.2/C7.3）；值仍由每轮快照锚定抽取（manifest 不预填值，
    C7.10）。解析器与出站由主装配统一提供。
    """
    parser_code_hash = _parser_code_hash()
    entries: list[dict[str, Any]] = []
    for source_id, url, artifact_type in _KNOWN_SOURCES:
        spec = _SOURCE_MANIFEST_SPECS[source_id]
        manifest = {
            "manifest_schema_version": "1",
            "source_id": source_id,
            "url": url,
            "artifact_type": artifact_type,
            "parser_id": _PARSER_ID,
            "parser_code_hash": parser_code_hash,
            "anchors": copy.deepcopy(spec["anchors"]),
            "fields": list(spec["fields"]),
            "slots": copy.deepcopy(spec["slots"]),
            "evidence_only_fields": list(spec["evidence_only_fields"]),
            "legal_state_rules": copy.deepcopy(spec["legal_state_rules"]),
            "proof_authority": spec["proof_authority"],
            "independent_counterpart": spec["independent_counterpart"],
        }
        entries.append({"source_id": source_id, "url": url, "manifest": manifest})
    return entries


class _SystemClock:
    """调度时钟（C6.4：运行时读系统时钟，不轮询忙等；测试注入假时钟）。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class _HttpxDocumentOutbound:
    """生产官方文档出站（SDD §5 管线阶段 1；Annex C C7.5）：

    - 仅精确 allowlist https URL 可出站（其余拒绝；无整域通配）；
    - TLS 验证开启、不跟随重定向（3xx 即来源失败）、超时 30s；
    - 快照 ≤10MB（超限 fail closed）；不携带任何负载/用户财务数据（REQ-17）。
    """

    def __init__(self, allowlist: frozenset[str] | set[str]) -> None:
        self._allowlist = frozenset(allowlist)

    def fetch(self, url: str, **kwargs: Any) -> bytes:
        if url not in self._allowlist:
            raise ConnectionError(f"allowlist 之外的 URL 拒绝出站（C7.5）：{url}")
        with httpx.Client(
            timeout=_OUTBOUND_TIMEOUT_SECONDS,
            follow_redirects=False,
            verify=True,
        ) as client:
            response = client.get(url)
        if response.status_code != 200:
            raise ConnectionError(
                f"来源返回非 200（{response.status_code}；不跟随重定向）：{url}"
            )
        body = response.content
        if len(body) > MAX_SNAPSHOT_BYTES:
            raise ValueError(f"快照超过 10MB 上限（SDD §5，fail closed）：{url}")
        return bytes(body)


def _start_scheduler_threads(scheduler: Scheduler) -> threading.Event:
    """启动调度后台线程（daemon）：首启即查＋周期 tick；返回停止事件。

    后台线程不阻塞事件循环/服务器启动，也不阻塞进程退出；检查作业经 Scheduler
    自身 singleflight/写锁串行化（REQ-17）。仅由 lifespan 调用（进程内 ASGI
    测试不执行 lifespan，测试经 app.state.update_scheduler 接缝注假件）。
    """
    stop = threading.Event()

    def _startup_worker() -> None:
        try:
            scheduler.startup()  # C6.6 首启即查
        except Exception:  # job 级失败已由调度器审计；不悬挂后台线程
            pass

    def _tick_worker() -> None:
        while not stop.wait(_TICK_INTERVAL_SECONDS):
            try:
                scheduler.tick()  # 到期/退避判定在 tick 内（无忙循环，C6.4）
            except Exception:  # 单轮意外异常不杀死周期循环
                continue

    for name, worker in (
        ("hktax-update-startup", _startup_worker),
        ("hktax-update-tick", _tick_worker),
    ):
        threading.Thread(target=worker, name=name, daemon=True).start()
    return stop


def create_app() -> FastAPI:
    """构建 ASGI 应用：初始化规则存储＋安全中间件＋路由＋统一错误处理＋调度。"""
    store = RuleStore(db_path_from_env())
    try:
        store.current()
    except AppError:
        store.publish(INITIAL_BUNDLE_CONTENT, evidence_refs=[])

    registry = SessionRegistry()
    # REQ-17/18：生产调度器＝C7.5 官方来源 allowlist＋生产 HTTPX 出站＋生产
    # 门禁绑定（独立参考 harness／候选引擎／已核必需 case 集合，C8.1/C8.4）；
    # tests/ui、tests/acceptance 经 app.state.update_scheduler 接缝注假件覆盖。
    sources = _known_source_entries()
    scheduler = Scheduler(
        store,
        clock=_SystemClock(),
        outbound=_HttpxDocumentOutbound(
            {str(source["url"]) for source in sources}
        ),
        sources=sources,
        harness=ReferenceHarness(),
        engine=CandidateEngine(),
        required_cases=REQUIRED_CASES,
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # C6.6 首启即查＋周期 tick：调度后台线程随 lifespan 启动/停止。
        stop_scheduler = _start_scheduler_threads(scheduler)
        try:
            yield
        finally:
            stop_scheduler.set()

    app = FastAPI(
        title="HK Tax Calculating Agent",
        version=ENGINE_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.store = store
    app.state.registry = registry
    app.state.update_scheduler = scheduler
    # REQ-14 模型出站接缝（C4.6 ⑦段；生产默认真实 HTTPX content=frozen_bytes；
    # 测试经 app.state.model_outbound 注入进程内假件）
    app.state.model_outbound = HttpxModelOutbound()
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app.add_middleware(SecurityMiddleware, registry=registry)
    register_routes(app, templates)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_payload(),
            headers={"Cache-Control": "no-store"},
        )

    return app


# 受控部署入口（README 启动命令：uvicorn app.main:app --host 127.0.0.1 ...）
app = create_app()


def run(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    *,
    workers: int = 1,
    proxy_headers: bool = False,
    runner: Any | None = None,
) -> None:
    """受控启动入口（C5.1：回环绑定＋单 worker＋不信任代理头）。

    - 非回环 host → app.web.security.LoopbackBindError（复用 M1 校验，
      精确集合 {127.0.0.1, localhost}）；
    - workers != 1 → 拒绝启动（进程内会话/同意状态依赖单进程）；
    - proxy_headers=True → 拒绝启动（C5.1/C5.2 不信任 X-Forwarded-*）；
    - 校验通过后经 runner（默认 uvicorn.run；依赖注入接缝，同
      Scheduler(outbound=…) 模式）以 workers=1、proxy_headers=False 启动。
    """
    validate_bind_host(host)
    if workers != 1:
        raise ValueError(
            "仅支持单 worker 启动（--workers 1）：进程内会话/同意状态依赖单进程"
        )
    if proxy_headers:
        raise ValueError(
            "不信任代理头（--no-proxy-headers）：C5.1/C5.2 禁用 X-Forwarded-* 透传"
        )
    if runner is None:
        import uvicorn

        runner = uvicorn.run
    runner(host=host, port=port, workers=1, proxy_headers=False)
