"""P2 W2 repair — replay 固定 profile/digest、五税 current C7 与 offline 提示。

Audit (ora-56) 项 2/4：
  - 005:62–66/895：engine/hash/schema/固定 P 身份＋digest 自洽为七目标共同
    兼容检查，一律 409 E_BUNDLE_INCOMPATIBLE 且先于 provider/context。
  - 005:76：五非两税保留既有 current-period C7 guard（不调用两税 provider、
    不扩 next）；005:71：trusted_offline 提示并入 replay warnings。
"""

from __future__ import annotations

import copy
import hashlib
import inspect

from app.core.hashing import canonical_json_bytes

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


def app_of(api):
    transport = getattr(api._client, "_transport", None)
    app = getattr(transport, "app", None)
    assert app is not None, "无法从 ASGITransport 取得 app 把手"
    return app


def store_of(api):
    return app_of(api).state.store


def registry_of(api):
    return app_of(api).state.registry


def sid_of(api) -> str:
    from app.config import SESSION_COOKIE_NAME

    api._token()
    raw = api._client.cookies.get(SESSION_COOKIE_NAME)
    sid = registry_of(api).sid_from_cookie(raw)
    assert sid, "未能解析测试会话 sid"
    return sid


def fresh_if_match(api) -> dict[str, str]:
    payload = api.get("/api/v1/meta/version").json()
    return {
        "If-Match": (
            f'"edit-{int(payload["session_epoch"])}-{int(payload["input_revision"])}"'
        )
    }


def _v2_year_block(start: int, finance: dict) -> dict:
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


def salary_facts(with_next: bool = True) -> dict:
    finance = {
        "has_assessable_salary_income": True,
        "source_classification": {"salary": "confirmed_in_scope"},
        "properties": [],
        "businesses": [],
        "deduction_items": [],
        "employment_income_nai": "240000",
        "mpf_mandatory_contributions": "9000",
    }
    target = _v2_year_block(2025, finance)
    years = {"2025_26": target}
    if with_next:
        target["provisional_paid"] = "0"
        years["2026_27"] = _v2_year_block(2026, finance)
    return {
        "year_of_assessment": "2025_26",
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": years,
        "hk_presence_days": [],
        "assumptions": [],
    }


def facts_for(tax_type: str) -> tuple[str, dict]:
    if tax_type == "salaries_tax":
        return "2.0.0", salary_facts()
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


def prepare(api, tax_type: str, schema: str, facts: dict) -> dict:
    r = api.post(
        PREPARE_PATH,
        json={"tax_type": tax_type, "schema_version": schema, "input": facts},
        headers=fresh_if_match(api),
    )
    assert r.status_code == 201, f"{tax_type}: prepare 须 201，{r.status_code}: {r.text[:300]}"
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
    assert r.status_code == 200, f"confirm 须 200，{r.status_code}: {r.text[:300]}"
    return r.json()["confirmation_id"]


def create_record(api, tax_type: str) -> tuple[str, dict]:
    schema, facts = facts_for(tax_type)
    prepared = prepare(api, tax_type, schema, facts)
    confirmation_id = confirm(api, prepared)
    r = api.post(
        EXECUTE_TARGETS[tax_type], json={"confirmation_id": confirmation_id}
    )
    assert r.status_code == 200, f"{tax_type}: execute 须 200，{r.status_code}: {r.text[:300]}"
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


def replace_stored_binding(api, sid: str, record_id: str, binding: dict) -> None:
    registry = registry_of(api)
    with registry._lock:
        registry._sessions[sid]["records"][record_id]["core"]["binding"] = (
            copy.deepcopy(binding)
        )


def initial_content(api) -> dict:
    from app.config import ENGINE_VERSION  # noqa: F401 - 保持 binding 同源

    return store_of(api).get_bundle(store_of(api).current()["bundle_id"])


def publish(api, content: dict) -> dict:
    from app.config import ENGINE_VERSION

    out = store_of(api).publish(content, evidence_refs=[])
    return {**out, "engine_version": ENGINE_VERSION}


def rehash_profile(profile: dict) -> str:
    core = {key: value for key, value in profile.items() if key != "semantic_digest"}
    return "sha256:" + hashlib.sha256(canonical_json_bytes(core)).hexdigest()


class RecordingGuard:
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


class ProviderSpy:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {}


def replay_direct(core: dict, store, *, provider=None, guard=None):
    from app.config import ENGINE_VERSION
    from app.web.records import recompute_for_replay

    kwargs: dict = {}
    if "availability_provider" in inspect.signature(recompute_for_replay).parameters:
        kwargs["availability_provider"] = provider
    if "guard" in inspect.signature(recompute_for_replay).parameters:
        kwargs["guard"] = guard
    return recompute_for_replay(core, store, ENGINE_VERSION, **kwargs)


OK_VERDICT = {"mode": "ok", "states": []}
BLOCKED_VERDICT = {"mode": "blocked", "states": []}
OFFLINE_VERDICT = {
    "mode": "trusted_offline",
    "notice": "offline",
    "states": ["trusted_offline"],
}


# ---------------------------------------------------------------------------
# 审计项 2：固定 P identity/digest 自洽核验（先于 provider）
# ---------------------------------------------------------------------------
def test_replay_tampered_fixed_profile_digest_is_409_before_provider(api) -> None:
    """篡改固定 profile 且同步重算 digest/原 bundle hash → salaries/property
    均 409 E_BUNDLE_INCOMPATIBLE，provider 0。"""
    from app.core.errors import AppError

    records = {}
    for tax_type in ("salaries_tax", "property_tax"):
        record_id, _ = create_record(api, tax_type)
        records[tax_type] = (record_id, record_core(api, record_id))

    content = copy.deepcopy(initial_content(api))
    profiles = content["frozen_semantics"]["profiles"]
    target = next(
        profile
        for profile in profiles
        if profile.get("profile_id") == "part5_shared_v2"
    )
    target["profile_version"] = 999
    target["semantic_digest"] = rehash_profile(target)
    tampered_binding = publish(api, content)

    sid = sid_of(api)
    for tax_type, (record_id, _) in records.items():
        replace_stored_binding(api, sid, record_id, tampered_binding)

    problems: list[str] = []
    for tax_type, (record_id, _) in records.items():
        resp = replay(api, record_id)
        if resp.status_code != 409:
            problems.append(
                f"{tax_type}: 固定 profile digest 篡改必须 409（005:66/:895），"
                f"实际 {resp.status_code}: {resp.text[:300]!r}"
            )
            continue
        code = (resp.json().get("error") or {}).get("code")
        if code != "E_BUNDLE_INCOMPATIBLE":
            problems.append(f"{tax_type}: 错误码须 E_BUNDLE_INCOMPATIBLE，实际 {code!r}")
    assert not problems, "固定 profile digest 兼容检查缺口：\n  - " + "\n  - ".join(
        problems
    )

    store = store_of(api)
    for tax_type, (record_id, core) in records.items():
        provider = ProviderSpy()
        direct = copy.deepcopy(core)
        direct["binding"] = copy.deepcopy(tampered_binding)
        try:
            replay_direct(direct, store, provider=provider)
        except AppError as exc:
            assert exc.code == "E_BUNDLE_INCOMPATIBLE", (tax_type, exc.code)
        else:
            raise AssertionError(f"{tax_type}: 篡改 profile 未拒绝")
        assert provider.calls == [], (
            f"{tax_type}: 兼容失败必须先于 provider（005:66），实际 {provider.calls!r}"
        )


def test_replay_engine_profile_identity_not_merely_engines_membership(api) -> None:
    """profile_id 被替换但 engines 仍包含目标税项 → 409（固定 P 身份核验）。"""
    record_id, _ = create_record(api, "property_tax")
    content = copy.deepcopy(initial_content(api))
    profiles = content["frozen_semantics"]["profiles"]
    target = next(
        profile
        for profile in profiles
        if profile.get("profile_id") == "property_per_unit_floor"
    )
    target["profile_id"] = "rogue_property_profile"
    binding = publish(api, content)
    replace_stored_binding(api, sid_of(api), record_id, binding)

    resp = replay(api, record_id)
    assert resp.status_code == 409, (
        f"仅 engines 包含税项不足以匹配固定 P 身份（005:895），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    assert (resp.json().get("error") or {}).get("code") == "E_BUNDLE_INCOMPATIBLE"


def test_replay_five_tax_fixed_profile_content_tamper_is_409_before_provider(
    api,
) -> None:
    """005:817/895：五税固定 profile 保留名称/engines 但改 rule（并同步 bundle
    hash）→ 全部 409 `E_BUNDLE_INCOMPATIBLE` 且 provider 0。"""
    from app.core.errors import AppError
    from app.rules.admission import FIXED_PROFILE_ID_BY_TAX

    created = {}
    for tax_type in FIVE_TAX_TYPES:
        record_id, _ = create_record(api, tax_type)
        created[tax_type] = (record_id, record_core(api, record_id))
    base = copy.deepcopy(initial_content(api))
    store = store_of(api)
    sid = sid_of(api)

    problems: list[str] = []
    for tax_type, (record_id, core) in created.items():
        expected_id = FIXED_PROFILE_ID_BY_TAX[tax_type]
        content = copy.deepcopy(base)
        target = next(
            profile
            for profile in content["frozen_semantics"]["profiles"]
            if profile.get("profile_id") == expected_id
        )
        target["rule"] = str(target.get("rule") or "") + "（tampered-rule）"
        binding = publish(api, content)
        replace_stored_binding(api, sid, record_id, binding)

        resp = replay(api, record_id)
        if resp.status_code != 409:
            problems.append(
                f"{tax_type}: 固定 profile 内容篡改必须 409（005:817/:895），"
                f"实际 {resp.status_code}: {resp.text[:300]!r}"
            )
        elif (resp.json().get("error") or {}).get("code") != (
            "E_BUNDLE_INCOMPATIBLE"
        ):
            problems.append(f"{tax_type}: 错误码须 E_BUNDLE_INCOMPATIBLE")

        provider = ProviderSpy()
        direct = copy.deepcopy(core)
        direct["binding"] = copy.deepcopy(binding)
        try:
            replay_direct(direct, store, provider=provider)
        except AppError as exc:
            if exc.code != "E_BUNDLE_INCOMPATIBLE":
                problems.append(f"{tax_type}: direct 错误码 {exc.code}")
        else:
            problems.append(f"{tax_type}: direct 未拒绝内容篡改")
        if provider.calls:
            problems.append(
                f"{tax_type}: 兼容失败必须先于 provider（005:66），"
                f"实际 {provider.calls!r}"
            )
    assert not problems, "五税固定 profile 内容锚缺口：\n  - " + "\n  - ".join(
        problems
    )


# ---------------------------------------------------------------------------
# 审计项 4：五税 current C7 guard（不调 provider、不扩 next）＋offline 提示
# ---------------------------------------------------------------------------
def test_replay_two_tax_trusted_offline_warning_preserved(api) -> None:
    """005:71：两税 admission 的 trusted_offline 固定提示必须进入 replay
    warnings（不得在 provider 返回后丢弃）。"""
    record_id, original = create_record(api, "salaries_tax")

    app = app_of(api)
    real_guard = app.state.update_scheduler
    app.state.update_scheduler = RecordingGuard({}, default=OFFLINE_VERDICT)
    try:
        resp = replay(api, record_id)
    finally:
        app.state.update_scheduler = real_guard
    assert resp.status_code == 200, (
        f"trusted_offline 不得阻断 current replay（005:71），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    payload = resp.json()
    assert payload["amounts"] == original["amounts"]
    warnings = payload.get("warnings") or []
    assert any("官方来源暂不可达" in str(text) for text in warnings), (
        f"两税 trusted_offline 提示必须传入 replay warnings（005:71），"
        f"实际 {warnings!r}"
    )


def test_replay_five_tax_c7_blocked_and_trusted_offline(api) -> None:
    """五税 replay：current guard blocked → 422 无结果；trusted_offline →
    200 且 warnings 含固定离线提示；不调用两税 provider、不查询 next。"""
    from app.core.errors import AppError

    created = {}
    for tax_type in FIVE_TAX_TYPES:
        record_id, _ = create_record(api, tax_type)
        created[tax_type] = (record_id, record_core(api, record_id))

    app = app_of(api)
    real_guard = app.state.update_scheduler
    try:
        app.state.update_scheduler = RecordingGuard({}, default=BLOCKED_VERDICT)
        for tax_type, (record_id, _) in created.items():
            resp = replay(api, record_id)
            assert resp.status_code == 422, (
                f"{tax_type}: 五税 current guard blocked 必须 422（005:76），"
                f"实际 {resp.status_code}: {resp.text[:300]!r}"
            )
            payload = resp.json()
            assert (payload.get("error") or {}).get("code") == (
                "E_RULES_STATE_UNVERIFIABLE"
            ), payload
            assert "amounts" not in payload and "status" not in payload

        guard = RecordingGuard({}, default=OFFLINE_VERDICT)
        app.state.update_scheduler = guard
        for tax_type, (record_id, original) in created.items():
            resp = replay(api, record_id)
            assert resp.status_code == 200, (
                f"{tax_type}: trusted_offline 不得阻断 current（005:71），"
                f"实际 {resp.status_code}: {resp.text[:300]!r}"
            )
            payload = resp.json()
            assert payload["amounts"] == original["amounts"], (
                f"{tax_type}: 五税金额必须沿用原结果"
            )
            warnings = payload.get("warnings") or []
            assert any("官方来源暂不可达" in str(text) for text in warnings), (
                f"{tax_type}: trusted_offline 固定提示必须传入 replay warnings"
                f"（005:71），实际 {warnings!r}"
            )
            assert "next" not in payload, "五税不得扩展 next-year（005:76）"
        assert all(period == "2025_26" for period in guard.calls), (
            f"五税只允许查询 current period，实际 {guard.calls!r}"
        )
        assert "2026_27" not in guard.calls
    finally:
        app.state.update_scheduler = real_guard

    # 直接调用：provider 0（五税不调用两税 availability provider）
    property_id, property_core = created["property_tax"]
    provider = ProviderSpy()
    guard_direct = RecordingGuard({})
    result = replay_direct(
        copy.deepcopy(property_core),
        store_of(api),
        provider=provider,
        guard=guard_direct,
    )
    assert provider.calls == [], (
        f"五税不得调用两税 current/next provider（005:76），实际 {provider.calls!r}"
    )
    assert guard_direct.calls == ["2025_26"], guard_direct.calls
    assert set(result.get("amounts") or {}) == set(AMOUNT_KEYS)
