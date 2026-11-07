"""P2 W2 Red — replay provider 合同（005 §2.2/§12.3；七目标兼容优先）。

REQ: REQ-12（replay 原 binding/原 bundle＋当前 guard 显式 context 闭合；
失败优先级）；REQ-13（回放≠当前重评、不改原记录）。

规格锚点（Approved，逐行）:
  - 005:39：`recompute_for_replay(core, store, engine_version, *,
    availability_provider) -> result`。
  - 005:42–55：compatible replay 的 bundle/context 来源＝「record 原 binding、
    原 bundle ＋ 当前可信 guard 对相关年度准入」。
  - 005:60–76：先 engine 完全相等→装入原 bundle→原 hash/schema/固定 profile；
    不符→409 `E_BUNDLE_INCOMPATIBLE`（**先于 context 查询；七目标统一**）；
    `context.binding` 始终为 record 原四元组，绝不调用 `pin_current`；
    current guard 缺失/畸形/unknown/blocked → 422
    `E_RULES_STATE_UNVERIFIABLE`；仅 next 缺/拒绝 → 保留当前确定金额、
    next=null、partial、salary balance=null；原束≠当前指针本身不拒绝；
    其余五税保持既有 current-period C7、无 next 概念。
  - 005:75：replay 不保存新 record、不改原 core/hash/input/steps、不产生或
    改变 revision 与确认能力。
  - 005:895：五入口同一 profile 核验；engine 不完全相等 → 全部 7 个旧执行
    目标 replay 拒绝（409），原查看/导出/打印不动。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - `availability_provider` 为可信可调用对象；先完成兼容性检查，通过后才
    调用（provider 的 guard 查询因此发生在兼容检查之后）。
  - replay 的 AvailabilityContext 以 record 原四元组为 binding；不得
    `pin_current`；两税 current/next 由 provider 的 guard 决定；五税不扩
    next、不调用 provider。
  - HTTP `POST /api/v1/records/replay`：409/422 wire 映射与 direct-call 一致。

本文件对 app.* 延迟导入；direct-call 兼容性节点在旧签名下仍执行真实兼容
检查（schema/profile 缺口因此是行为断言失败），新增参数存在时同时断言
provider 未被提前调用。
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Mapping, MutableMapping

BASE_ORIGIN = "http://127.0.0.1:8000"
PREPARE_PATH = "/api/v1/calc/prepare"
CONFIRM_PATH = "/api/v1/calc/confirm"
REPLAY_PATH = "/api/v1/records/replay"

EXECUTE_TARGETS = {
    "salaries_tax": "/api/v1/calc/salaries-tax",
    "profits_tax": "/api/v1/calc/profits-tax",
    "property_tax": "/api/v1/calc/property-tax",
    "personal_assessment": "/api/v1/personal-assessment/compare",
    "stamp_property": "/api/v1/calc/stamp-duty/property",
    "stamp_stock": "/api/v1/calc/stamp-duty/stock",
    "stamp_lease": "/api/v1/calc/stamp-duty/lease",
}
TWO_TAX_TYPES = ("salaries_tax", "personal_assessment")
FIVE_TAX_TYPES = (
    "profits_tax",
    "property_tax",
    "stamp_property",
    "stamp_stock",
    "stamp_lease",
)
AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)


# ---------------------------------------------------------------------------
# 事实构造（最小合法；与 UI/验收 helper 同源）
# ---------------------------------------------------------------------------
def _year_block(start: int, finance: dict) -> dict:
    return {
        "participants": [{"person_id": "p1", "role": "self"}],
        "subject_facts": {},
        "household": {
            "marriage": [
                {
                    "from": f"{start}-04-01",
                    "to": f"{start + 1}-04-01",
                    "value": "unmarried",
                }
            ],
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        "relations": [],
        "care_payments": [],
        "claims": [],
        "financials": {"p1": copy.deepcopy(finance)},
        "assessment": {
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
        },
        "manual_issues": [],
    }


def salary_facts(*, with_next: bool) -> dict:
    finance = {
        "has_assessable_salary_income": True,
        "source_classification": {"salary": "confirmed_in_scope"},
        "properties": [],
        "businesses": [],
        "deduction_items": [],
        "employment_income_nai": "240000",
        "mpf_mandatory_contributions": "9000",
    }
    target = _year_block(2025, finance)
    years = {"2025_26": target}
    if with_next:
        target["provisional_paid"] = "0"
        years["2026_27"] = _year_block(2026, finance)
    return {
        "year_of_assessment": "2025_26",
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": years,
        "hk_presence_days": [],
        "assumptions": [],
    }


def personal_assessment_facts() -> dict:
    return {
        "year_of_assessment": "2025_26",
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": {
            "2025_26": {
                "participants": [{"person_id": "p1", "role": "self"}],
                "subject_facts": {
                    "p1": {
                        "ordinarily_resident_hk": [
                            {"from": "2026-06-01", "to": "2026-06-02", "value": True}
                        ]
                    }
                },
                "household": {
                    "marriage": [
                        {"from": "2025-04-01", "to": "2026-04-01", "value": "unmarried"},
                        {"from": "2026-06-01", "to": "2026-06-02", "value": "unmarried"},
                    ],
                    "living_apart": [],
                    "child_nomination": {"person_id": None, "status": "none"},
                },
                "relations": [],
                "financials": {
                    "p1": {
                        "employment_income_nai": "250000",
                        "mpf_mandatory_contributions": "0",
                        "has_assessable_salary_income": True,
                        "has_assessable_property_income": True,
                        "has_assessable_business_income": False,
                        "has_income_assessable_under_ordinance": True,
                        "source_classification": {
                            "salary": "confirmed_in_scope",
                            "property": "confirmed_in_scope",
                            "business": "confirmed_in_scope",
                        },
                        "properties": [
                            {
                                "property_id": "h1",
                                "share": "1/1",
                                "rent_received": "120000",
                                "rates_paid_by_owner": {
                                    "amount": "0",
                                    "agreed": False,
                                    "actually_paid": False,
                                },
                                "irrecoverable_rent": "0",
                                "deposit_offsets": "0",
                                "mortgage_interest": "0",
                            }
                        ],
                        "businesses": [],
                        "deduction_items": [],
                    }
                },
                "care_payments": [],
                "claims": [],
                "assessment": {
                    "reference_date": "2026-06-01",
                    "basis": "hypothetical",
                    "actual": {
                        "salary_joint_elected": False,
                        "salary_joint_assessed": False,
                        "pa": {"p1": {"mode": "none"}},
                        "history": "ordinary",
                    },
                    "candidates": ["no_pa", "pa_self"],
                    "selection": "best",
                },
                "manual_issues": [],
                "provisional_paid": "0",
            }
        },
        "hk_presence_days": [],
        "assumptions": [],
    }


def facts_for(tax_type: str) -> tuple[str, dict]:
    if tax_type == "salaries_tax":
        return "2.0.0", salary_facts(with_next=True)
    if tax_type == "personal_assessment":
        return "2.0.0", personal_assessment_facts()
    if tax_type == "profits_tax":
        return "1.0.0", {
            "year_of_assessment": "2025_26",
            "entity_kind": "sole_proprietorship",
            "assessable_profit": "600000",
            "two_tier": {
                "connected_entities": [],
                "election_made": {"value": True},
                "no_other_election_same_year": {"value": True},
            },
        }
    if tax_type == "property_tax":
        return "1.0.0", {
            "year_of_assessment": "2025_26",
            "properties": [
                {
                    "property_id": "p-1",
                    "rent_received": "120000",
                    "rates_paid_by_owner": {
                        "amount": "0",
                        "agreed": {"value": True},
                        "actually_paid": {"value": True},
                    },
                    "irrecoverable_rent": "0",
                    "deposit_offsets": "0",
                }
            ],
        }
    if tax_type == "stamp_property":
        return "1.0.0", {
            "instrument_date": "2025-06-20",
            "instrument_kind": "agreement_for_sale",
            "property_class": "residential",
            "consideration": "6000000",
            "value": "6000000",
        }
    if tax_type == "stamp_stock":
        return "1.0.0", {
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
    if tax_type == "stamp_lease":
        return "1.0.0", {
            "instrument_date": "2025-06-20",
            "term_kind": "fixed",
            "term_start": "2025-07-01",
            "term_end": "2027-06-30",
            "rent_input_mode": "fixed_monthly",
            "monthly_rent": "10000",
            "premium": "0",
        }
    raise AssertionError(f"未列入的执行目标：{tax_type}")


# ---------------------------------------------------------------------------
# HTTP/应用 helper（app 由 root `api` fixture 的 ASGITransport 提供）
# ---------------------------------------------------------------------------
def app_of(api):
    transport = getattr(api._client, "_transport", None)
    app = getattr(transport, "app", None)
    assert app is not None, "无法从 ASGITransport 取得 app 把手（测试接缝）"
    return app


def registry_of(api):
    registry = getattr(app_of(api).state, "registry", None)
    assert registry is not None, "app.state.registry 缺失（C5.3）"
    return registry


def store_of(api):
    store = getattr(app_of(api).state, "store", None)
    assert store is not None, "app.state.store 缺失"
    return store


def sid_of(api) -> str:
    from app.config import SESSION_COOKIE_NAME

    api._token()
    raw = api._client.cookies.get(SESSION_COOKIE_NAME)
    sid = registry_of(api).sid_from_cookie(raw)
    assert sid, "未能解析测试会话 sid（C5.3）"
    return sid


def fresh_if_match(api) -> dict[str, str]:
    r = api.get("/api/v1/meta/version")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "session_epoch" in payload and "input_revision" in payload, payload
    return {
        "If-Match": (
            f'"edit-{int(payload["session_epoch"])}-{int(payload["input_revision"])}"'
        )
    }


def runtime_versions(api) -> tuple[int, int]:
    payload = api.get("/api/v1/meta/version").json()
    return int(payload["session_epoch"]), int(payload["input_revision"])


def prepare(api, tax_type: str, schema: str, facts: dict) -> dict:
    r = api.post(
        PREPARE_PATH,
        json={"tax_type": tax_type, "schema_version": schema, "input": facts},
        headers=fresh_if_match(api),
    )
    assert r.status_code == 201, (
        f"{tax_type}: prepare 须 201，实际 {r.status_code}: {r.text[:300]!r}"
    )
    return r.json()


def confirm(api, prepared: dict) -> str:
    r = api.post(
        CONFIRM_PATH,
        json={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": True,
        },
    )
    assert r.status_code == 200, f"confirm 须 200，实际 {r.status_code}: {r.text[:300]!r}"
    return r.json()["confirmation_id"]


def create_record(api, tax_type: str) -> tuple[str, dict]:
    schema, facts = facts_for(tax_type)
    prepared = prepare(api, tax_type, schema, facts)
    confirmation_id = confirm(api, prepared)
    r = api.post(
        EXECUTE_TARGETS[tax_type], json={"confirmation_id": confirmation_id}
    )
    assert r.status_code == 200, (
        f"{tax_type}: execute 须 200，实际 {r.status_code}: {r.text[:300]!r}"
    )
    payload = r.json()
    record_id = payload.get("record_id")
    assert record_id, payload
    return record_id, payload


def record_core(api, record_id: str) -> dict:
    r = api.get(f"/api/v1/records/{record_id}")
    assert r.status_code == 200, r.text
    return r.json()["record"]


def replay(api, record_id: str):
    return api.post(REPLAY_PATH, json={"record_id": record_id})


def record_ids(api, sid: str) -> frozenset[str]:
    registry = registry_of(api)
    with registry._lock:
        sessions = getattr(registry, "_sessions", None)
        assert isinstance(sessions, MutableMapping), (
            "INTERFACE_HELD：registry._sessions 权威容器不可观察（PQA-03）"
        )
        assert sid in sessions, f"状态前置失败：sid 不在 _sessions（{sid!r}）"
        records = sessions[sid].get("records")
        assert isinstance(records, Mapping), "INTERFACE_HELD：records 不可观察"
        return frozenset(records.keys())


def replace_stored_binding(api, sid: str, record_id: str, binding: dict) -> None:
    """测试接缝：改写权威容器内原记录的 binding（replay 只读取，不写回）。"""
    registry = registry_of(api)
    with registry._lock:
        session = registry._sessions[sid]
        session["records"][record_id]["core"]["binding"] = copy.deepcopy(binding)


# ---------------------------------------------------------------------------
# store/guard/provider helper
# ---------------------------------------------------------------------------
def initial_binding(api) -> dict:
    from app.config import ENGINE_VERSION

    return {**store_of(api).current(), "engine_version": ENGINE_VERSION}


def initial_content(api) -> dict:
    binding = initial_binding(api)
    return store_of(api).get_bundle(binding["bundle_id"])


def publish_alt_bundle(api) -> dict:
    """发布第二份结构合法束（指针切换；原 bundle 仍可按 id 载入）。"""
    from app.config import ENGINE_VERSION

    content = copy.deepcopy(initial_content(api))
    digests = list(content.get("evidence_digests") or [])
    digests.append("sha256:" + "ab" * 32)
    content["evidence_digests"] = digests
    out = store_of(api).publish(content, evidence_refs=[])
    return {**out, "engine_version": ENGINE_VERSION}


def publish_without_engine_profile(api, tax_type: str, base: dict | None = None) -> dict:
    """发布删除该引擎 frozen profile 的束（profile 不支持）。"""
    from app.config import ENGINE_VERSION

    content = copy.deepcopy(base if base is not None else initial_content(api))
    frozen = content.get("frozen_semantics") or {}
    profiles = list(frozen.get("profiles") or [])
    kept = [
        profile
        for profile in profiles
        if tax_type not in (profile.get("engines") or [])
    ]
    assert len(kept) < len(profiles), (
        f"前置：初始束必须声明 {tax_type} 的 frozen profile（005 §15.1）"
    )
    frozen["profiles"] = kept
    content["frozen_semantics"] = frozen
    out = store_of(api).publish(content, evidence_refs=[])
    return {**out, "engine_version": ENGINE_VERSION}


class ProviderSpy:
    """可信 provider 假件：兼容失败时不得被调用（005:66）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {}


class RecordingGuard:
    """guard 接缝假件：按期间返回判定并记录查询（provider 的可信来源）。"""

    def __init__(self, verdicts: dict[str, dict], default: dict | None = None) -> None:
        self.verdicts = dict(verdicts)
        self.default = default
        self.calls: list[str] = []

    def availability(self, period: str) -> dict:
        self.calls.append(period)
        if period in self.verdicts:
            return copy.deepcopy(self.verdicts[period])
        if self.default is not None:
            return copy.deepcopy(self.default)
        return {"mode": "ok", "states": []}


OK_VERDICT = {"mode": "ok", "notice": None, "states": []}
BLOCKED_VERDICT = {"mode": "blocked", "states": []}
UNKNOWN_VERDICT = {"mode": "ok", "states": ["unknown"]}


class RaisingGuard:
    def availability(self, period: str) -> dict:
        raise RuntimeError("guard-query-marker")


# ---------------------------------------------------------------------------
# direct-call replay（兼容性节点在当前旧签名下仍执行真实检查）
# ---------------------------------------------------------------------------
def replay_for_compat(core: dict, store, provider: ProviderSpy):
    from app.config import ENGINE_VERSION
    from app.web.records import recompute_for_replay

    signature = inspect.signature(recompute_for_replay)
    if "availability_provider" in signature.parameters:
        return recompute_for_replay(
            core, store, ENGINE_VERSION, availability_provider=provider
        )
    return recompute_for_replay(core, store, ENGINE_VERSION)


def direct_core(tax_type: str, binding: dict) -> dict:
    return {
        "tax_type": tax_type,
        "period": "2025_26",
        "input": {},
        "binding": copy.deepcopy(binding),
        "status": "complete",
        "amounts": {key: None for key in AMOUNT_KEYS},
        "steps": [],
    }


# ---------------------------------------------------------------------------
# 节点 1：兼容性先于 provider（七目标×五类 → 409）
# ---------------------------------------------------------------------------
def test_replay_compatibility_precedes_provider_all_targets(api) -> None:
    """005:60–76/:895：engine/hash/schema/profile/原束缺失 → 409
    `E_BUNDLE_INCOMPATIBLE`，且 provider 调用数=0（先于 context 查询）。"""
    from app.core.errors import AppError
    from app.config import ENGINE_VERSION

    store = store_of(api)
    base = initial_binding(api)
    base_content = initial_content(api)

    problems: list[str] = []
    for tax_type in EXECUTE_TARGETS:
        profile_binding = publish_without_engine_profile(api, tax_type, base_content)
        cases = {
            "engine": {**base, "engine_version": "0.0.0"},
            "bundle-missing": {**base, "bundle_id": "b-does-not-exist"},
            "hash": {**base, "bundle_hash": "sha256:" + "00" * 32},
            "schema": {**base, "rules_schema_version": "1.0.0"},
            "profile": {**profile_binding, "engine_version": ENGINE_VERSION},
        }
        for label, binding in cases.items():
            provider = ProviderSpy()
            core = direct_core(tax_type, binding)
            try:
                replay_for_compat(core, store, provider)
            except AppError as exc:
                if exc.code != "E_BUNDLE_INCOMPATIBLE":
                    problems.append(
                        f"{tax_type}/{label}: 期望 409 E_BUNDLE_INCOMPATIBLE，"
                        f"实际 {exc.code}（{exc.http_status}）"
                    )
            except Exception as exc:  # noqa: BLE001 - 收集后统一报告
                problems.append(f"{tax_type}/{label}: 非 AppError 异常 {exc!r}")
            else:
                problems.append(
                    f"{tax_type}/{label}: 未拒绝（兼容性检查未先于 context）"
                )
            if provider.calls:
                problems.append(
                    f"{tax_type}/{label}: provider 被提前调用 {provider.calls!r}"
                )

    assert not problems, (
        "005:60–76 兼容性优先缺口（七目标统一 409，先于 provider）：\n  - "
        + "\n  - ".join(problems)
    )
    # 前置自证：初始束确实声明了七引擎 profile（profile 类负例才成立）
    assert base_content.get("frozen_semantics"), "规则束缺 frozen_semantics"


# ---------------------------------------------------------------------------
# 节点 2：兼容通过后调 provider；binding 为原四元组；绝不 pin_current
# ---------------------------------------------------------------------------
def test_replay_provider_binding_original_and_no_pin_current(api, monkeypatch) -> None:
    """005:62/:74/:895：原束≠当前指针仍按原 binding 成功；provider 在兼容后
    被调用；context.binding 恒为 record 原四元组；replay 不调用 pin_current。"""
    record_id, _ = create_record(api, "salaries_tax")
    original = record_core(api, record_id)
    original_binding = copy.deepcopy(original["binding"])

    # 指针切换：原束仍可按 id 载入（005:74）
    publish_alt_bundle(api)
    current = store_of(api).current()
    assert current["bundle_hash"] != original_binding["bundle_hash"], (
        "前置：当前指针必须已不同于原 binding（005:74）"
    )

    guard = RecordingGuard({})
    app = app_of(api)
    real_guard = app.state.update_scheduler
    app.state.update_scheduler = guard
    pin_calls: list[int] = []
    original_pin = store_of(api).pin_current

    def pin_spy():
        pin_calls.append(1)
        return original_pin()

    monkeypatch.setattr(store_of(api), "pin_current", pin_spy)

    captured: list[dict] = []
    import app.web.records as records_module

    original_run_engine = records_module.run_engine

    def run_engine_spy(tax_type, facts, bundle, *, availability=None):
        captured.append(
            {
                "tax_type": tax_type,
                "bundle": copy.deepcopy(bundle),
                "availability": copy.deepcopy(availability),
            }
        )
        return original_run_engine(
            tax_type, facts, bundle, availability=availability
        )

    monkeypatch.setattr(records_module, "run_engine", run_engine_spy)

    try:
        resp = replay(api, record_id)
    finally:
        app.state.update_scheduler = real_guard
    assert set(guard.calls) >= {"2025_26", "2026_27"}, (
        "兼容检查通过后 provider 必须查询 current/next guard（005:62/:66），"
        f"实际 {guard.calls!r}"
    )
    assert resp.status_code == 200, (
        f"原束兼容且 guard 允许时 replay 必须 200（005:74），"
        f"实际 {resp.status_code}: {resp.text[:400]!r}"
    )
    payload = resp.json()
    assert payload["binding"] == original_binding, (
        "replay 响应必须保留 record 原四元组 binding（005:62/:74）"
    )
    assert pin_calls == [], (
        f"replay 绝不调用 pin_current（005:62），实际 {len(pin_calls)} 次"
    )
    assert len(captured) == 1, f"replay 必须恰执行一次引擎，实际 {captured!r}"
    from app.core.hashing import bundle_content_hash

    assert bundle_content_hash(captured[0]["bundle"]) == original_binding["bundle_hash"], (
        "replay 必须装入 record 原 bundle（不得改用当前指针）"
    )
    availability = captured[0]["availability"]
    assert isinstance(availability, Mapping), (
        "replay 必须向两税引擎传显式 AvailabilityContext（005:42–55）"
    )
    assert availability.get("binding") == original_binding, (
        "context.binding 恒为 record 原四元组（005:62）"
    )


# ---------------------------------------------------------------------------
# 节点 3：两税 current guard 缺失/unknown/blocked/不可核验 → 422；恢复 200
# ---------------------------------------------------------------------------
def test_replay_two_tax_current_guard_unverifiable_then_recovers(api) -> None:
    """005:66–69：原束兼容但公开 guard 缺失/畸形/unknown/blocked → 422
    `E_RULES_STATE_UNVERIFIABLE`，无结果；guard 恢复后 replay 200。"""
    records = {}
    for tax_type in TWO_TAX_TYPES:
        record_id, _ = create_record(api, tax_type)
        records[tax_type] = (record_id, record_core(api, record_id))

    app = app_of(api)
    real_guard = app.state.update_scheduler
    try:
        for tax_type, (record_id, _) in records.items():
            for label, fake in (
                ("missing", None),
                ("unknown", RecordingGuard({}, default=UNKNOWN_VERDICT)),
                ("blocked", RecordingGuard({}, default=BLOCKED_VERDICT)),
                ("query-failed", RaisingGuard()),
            ):
                app.state.update_scheduler = fake
                try:
                    resp = replay(api, record_id)
                finally:
                    app.state.update_scheduler = real_guard
                assert resp.status_code == 422, (
                    f"{tax_type}/{label}: current guard 不可核验必须 422（005:68），"
                    f"实际 {resp.status_code}: {resp.text[:300]!r}"
                )
                payload = resp.json()
                assert payload.get("error", {}).get("code") == (
                    "E_RULES_STATE_UNVERIFIABLE"
                ), payload
                assert "amounts" not in payload and "status" not in payload, (
                    f"{tax_type}/{label}: 不可核验不得返回结果字段"
                )

        # —— guard 恢复：原束兼容 → 200（current 可用）——
        app.state.update_scheduler = RecordingGuard({})
        for tax_type, (record_id, original) in records.items():
            resp = replay(api, record_id)
            assert resp.status_code == 200, (
                f"{tax_type}: guard 恢复后 replay 必须 200（005:71），"
                f"实际 {resp.status_code}: {resp.text[:400]!r}"
            )
            payload = resp.json()
            assert payload["binding"] == original["binding"]
            assert payload["amounts"]["before_reduction"] == (
                original["amounts"]["before_reduction"]
            )
    finally:
        app.state.update_scheduler = real_guard


# ---------------------------------------------------------------------------
# 节点 4：仅 next 不可用 → 保留当前金额；next=null、partial、balance=null
# ---------------------------------------------------------------------------
def test_replay_next_only_unavailable_keeps_current_amounts(api) -> None:
    """005:70：当前年可用、仅下一年缺/拒绝 → 保留当前确定金额；next=null、
    partial、salary balance=null。"""
    app = app_of(api)
    real_guard = app.state.update_scheduler

    salary_id, _ = create_record(api, "salaries_tax")
    salary_core = record_core(api, salary_id)
    assert salary_core["amounts"]["next_provisional"] is not None, (
        "前置：完整薪俸记录必须含 next_provisional（005 §8）"
    )
    pa_id, _ = create_record(api, "personal_assessment")
    pa_core = record_core(api, pa_id)

    app.state.update_scheduler = RecordingGuard(
        {"2025_26": OK_VERDICT, "2026_27": BLOCKED_VERDICT}
    )
    try:
        resp = replay(api, salary_id)
        assert resp.status_code == 200, (
            "仅 next 被 guard 抑制不得拒绝本次重算（005:70），"
            f"实际 {resp.status_code}: {resp.text[:400]!r}"
        )
        payload = resp.json()
        assert payload["status"] == "partial", (
            f"仅 next 不可用必须 partial（005:70），实际 {payload['status']!r}"
        )
        amounts = payload["amounts"]
        assert amounts["before_reduction"] == (
            salary_core["amounts"]["before_reduction"]
        )
        assert amounts["final_after_reduction"] == (
            salary_core["amounts"]["final_after_reduction"]
        ), "当前确定金额必须保留（005:70）"
        assert amounts["next_provisional"] is None, (
            f"next 不可用必须 null（不得借用 latest），实际 {amounts!r}"
        )
        assert amounts["balance"] is None, (
            f"salary 仅 next 被抑制时 balance=null（005:70），实际 {amounts!r}"
        )

        resp_pa = replay(api, pa_id)
        assert resp_pa.status_code == 200, resp_pa.text[:400]
        pa_payload = resp_pa.json()
        assert pa_payload["status"] == "partial"
        assert pa_payload["amounts"]["final_after_reduction"] == (
            pa_core["amounts"]["final_after_reduction"]
        ), "PA 当前确定金额必须保留（005:70）"
        assert pa_payload["amounts"]["next_provisional"] is None
        assert pa_payload["amounts"]["balance"] is None
    finally:
        app.state.update_scheduler = real_guard


# ---------------------------------------------------------------------------
# 节点 5：原 bundle ≠ 当前指针、guard 允许 → 成功（005:74）
# ---------------------------------------------------------------------------
def test_replay_original_bundle_not_current_pointer_succeeds(api) -> None:
    """005:74：原 bundle ≠ current pointer 本身不拒绝；同引擎+guard 允许
    → replay 成功且金额/状态来自原束重算。"""
    record_id, executed = create_record(api, "salaries_tax")
    original = record_core(api, record_id)
    publish_alt_bundle(api)
    current = store_of(api).current()
    assert current["bundle_id"] != original["binding"]["bundle_id"], (
        "前置：当前指针必须已切换"
    )

    app = app_of(api)
    real_guard = app.state.update_scheduler
    app.state.update_scheduler = RecordingGuard({})
    try:
        resp = replay(api, record_id)
    finally:
        app.state.update_scheduler = real_guard

    assert resp.status_code == 200, (
        "原束兼容且 guard 允许的成功 replay 必须保留（005:74），"
        f"实际 {resp.status_code}: {resp.text[:400]!r}"
    )
    payload = resp.json()
    assert payload["binding"] == original["binding"]
    assert payload["status"] == executed["status"]
    assert payload["amounts"] == original["amounts"], (
        "replay 必须按原束重算并保留原记录金额（005:74/:75）"
    )


# ---------------------------------------------------------------------------
# 节点 6：五非两税目标不调用 provider、不扩 next；profile 不支持 → 409
# ---------------------------------------------------------------------------
def test_replay_five_other_targets_no_next_and_profile_gate(api) -> None:
    """005:66/:76：五税保持既有 C7、无 next、不调用两税 provider；profile
    不支持仍 409（七目标统一兼容检查）。"""
    created: dict[str, tuple[str, dict]] = {}
    for tax_type in FIVE_TAX_TYPES:
        record_id, executed = create_record(api, tax_type)
        created[tax_type] = (record_id, record_core(api, record_id))

    guard = RecordingGuard({})
    app = app_of(api)
    real_guard = app.state.update_scheduler
    app.state.update_scheduler = guard
    try:
        problems: list[str] = []
        for tax_type, (record_id, original) in created.items():
            resp = replay(api, record_id)
            if resp.status_code != 200:
                problems.append(
                    f"{tax_type}: 五税既有 replay 必须 200，实际 "
                    f"{resp.status_code}: {resp.text[:200]!r}"
                )
                continue
            payload = resp.json()
            if payload.get("binding") != original["binding"]:
                problems.append(f"{tax_type}: binding 必须保留原四元组")
            if payload.get("status") != original["status"]:
                problems.append(f"{tax_type}: status 必须沿用原结果")
            if payload.get("amounts") != original["amounts"]:
                problems.append(f"{tax_type}: amounts 必须沿用原结果")
            if "next" in payload:
                problems.append(f"{tax_type}: 五税不得扩展 next-year（005:76）")
            if set(payload.get("amounts") or {}) != set(AMOUNT_KEYS):
                problems.append(f"{tax_type}: 五税金额键集合必须保持六键")
        assert not problems, "五税 replay 既有行为缺口：\n  - " + "\n  - ".join(
            problems
        )
        assert guard.calls == ["2025_26"] * len(created), (
            "五税必须保留既有 current-period C7 guard 检查，且不得查询 next/"
            f"两税 provider（005:76），实际 {guard.calls!r}"
        )
    finally:
        app.state.update_scheduler = real_guard

    # —— profile 不支持 → 409（先于 context；七目标统一）——
    sid = sid_of(api)
    base_content = initial_content(api)
    for tax_type, (record_id, _) in created.items():
        binding = publish_without_engine_profile(api, tax_type, base_content)
        replace_stored_binding(api, sid, record_id, binding)
        guard.calls.clear()
        app.state.update_scheduler = guard
        try:
            resp = replay(api, record_id)
        finally:
            app.state.update_scheduler = real_guard
        assert resp.status_code == 409, (
            f"{tax_type}: profile 不支持必须 409 E_BUNDLE_INCOMPATIBLE"
            f"（005:66），实际 {resp.status_code}: {resp.text[:300]!r}"
        )
        assert resp.json().get("error", {}).get("code") == "E_BUNDLE_INCOMPATIBLE"
        assert guard.calls == [], (
            f"{tax_type}: 兼容性检查必须先于 context 查询（005:66），"
            f"实际 guard 查询 {guard.calls!r}"
        )


# ---------------------------------------------------------------------------
# 节点 7：replay 不保存新记录、不改原 core/hash/steps/revision/确认能力
# ---------------------------------------------------------------------------
def test_replay_does_not_mutate_record_or_capability(api) -> None:
    """005:75：replay 不保存新 record、不改原 core/hash/input/steps、不产生
    或改变 revision 与确认能力（deep compare）。"""
    from app.core.hashing import calculation_core_hash

    record_id, _ = create_record(api, "salaries_tax")
    before = record_core(api, record_id)
    before_hash = calculation_core_hash(before)
    records_before = record_ids(api, sid_of(api))

    # 待用确认能力（replay 前后必须仍可执行）
    prepared = prepare(api, "salaries_tax", "2.0.0", salary_facts(with_next=True))
    pending_confirmation = confirm(api, prepared)
    epoch_before, revision_before = runtime_versions(api)

    app = app_of(api)
    real_guard = app.state.update_scheduler
    app.state.update_scheduler = RecordingGuard({})
    try:
        resp = replay(api, record_id)
        assert resp.status_code == 200, (
            f"兼容 replay 必须 200 才能验证只读性，实际 {resp.status_code}: "
            f"{resp.text[:300]!r}"
        )
        after = record_core(api, record_id)
        assert after == before, (
            "replay 不得改写原记录 core/input/steps/binding（005:75）"
        )
        assert calculation_core_hash(after) == before_hash, (
            "replay 不得改变原 core hash（005:75）"
        )
        assert record_ids(api, sid_of(api)) == records_before, (
            "replay 不得保存新 record（005:75）"
        )
        assert runtime_versions(api) == (epoch_before, revision_before), (
            "replay 不得改变 revision/epoch（005:75）"
        )
        execution = api.post(
            EXECUTE_TARGETS["salaries_tax"],
            json={"confirmation_id": pending_confirmation},
        )
        assert execution.status_code == 200, (
            "replay 不得改变既有确认能力（005:75），"
            f"实际 {execution.status_code}: {execution.text[:300]!r}"
        )
    finally:
        app.state.update_scheduler = real_guard


# ---------------------------------------------------------------------------
# 节点 8：HTTP wire 映射（409/422，以及恢复后的 200）
# ---------------------------------------------------------------------------
def test_replay_http_wire_mapping_409_and_422(api) -> None:
    """005:66–76：POST /api/v1/records/replay 的 409/422 wire 映射；guard
    恢复后同一原束 200（不得始终 422）。"""
    record_id, _ = create_record(api, "salaries_tax")
    original = record_core(api, record_id)

    # —— engine 不完全相等 → 409（原有严格相等，005:895）——
    import app.api.routes as routes_module
    import app.config as config_module

    saved_config = config_module.ENGINE_VERSION
    saved_routes = routes_module.ENGINE_VERSION
    config_module.ENGINE_VERSION = "9.9.9"
    routes_module.ENGINE_VERSION = "9.9.9"
    try:
        resp_engine = replay(api, record_id)
    finally:
        config_module.ENGINE_VERSION = saved_config
        routes_module.ENGINE_VERSION = saved_routes
    assert resp_engine.status_code == 409, (
        f"engine 不完全相等必须 409（005:895），实际 {resp_engine.status_code}: "
        f"{resp_engine.text[:300]!r}"
    )
    assert resp_engine.json().get("error", {}).get("code") == (
        "E_BUNDLE_INCOMPATIBLE"
    )

    # —— current guard blocked → 422（无结果）——
    app = app_of(api)
    real_guard = app.state.update_scheduler
    app.state.update_scheduler = RecordingGuard({}, default=BLOCKED_VERDICT)
    try:
        resp_blocked = replay(api, record_id)
    finally:
        app.state.update_scheduler = real_guard
    assert resp_blocked.status_code == 422, (
        f"current guard 阻断必须 422（005:68），实际 {resp_blocked.status_code}: "
        f"{resp_blocked.text[:300]!r}"
    )
    blocked_payload = resp_blocked.json()
    assert blocked_payload.get("error", {}).get("code") == (
        "E_RULES_STATE_UNVERIFIABLE"
    )
    assert "amounts" not in blocked_payload and "status" not in blocked_payload

    # —— guard 恢复 → 200（证明 422 由 guard 驱动，而非 replay 永久失败）——
    app.state.update_scheduler = RecordingGuard({})
    try:
        resp_ok = replay(api, record_id)
    finally:
        app.state.update_scheduler = real_guard
    assert resp_ok.status_code == 200, (
        "guard 恢复后同一原束 replay 必须 200（005:71），"
        f"实际 {resp_ok.status_code}: {resp_ok.text[:400]!r}"
    )
    assert resp_ok.json()["binding"] == original["binding"]
    assert resp_ok.json()["amounts"] == original["amounts"]
