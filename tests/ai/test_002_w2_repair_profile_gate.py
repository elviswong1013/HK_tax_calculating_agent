"""P2 W2 repair3 — 五税固定 profile 内容锚覆盖 prepare/execute/AI calculate。

复审计2 阻断 1：`verify_bundle_admission` 及其 runtime/prepare/execute/
AI calculate 入口必须与 strict replay/AI precheck 同强度核验五税固定 profile
完整内容；保留名称/engines 但改 rule（并同步 bundle hash）→ 拒绝、无金额。
"""

from __future__ import annotations

import copy
import hashlib
import time

import pytest

from _ai_fakes import ai_round, tool_results_of, wire_tool_calls_body

PREPARE_PATH = "/api/v1/calc/prepare"
EXECUTE_TARGETS = {
    "profits_tax": "/api/v1/calc/profits-tax",
    "property_tax": "/api/v1/calc/property-tax",
    "stamp_property": "/api/v1/calc/stamp-duty/property",
    "stamp_stock": "/api/v1/calc/stamp-duty/stock",
    "stamp_lease": "/api/v1/calc/stamp-duty/lease",
}
MSG = [{"role": "user", "content": "請幫我估算稅項"}]


def _facts_for(tax_type: str) -> dict:
    if tax_type == "profits_tax":
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
    if tax_type == "property_tax":
        return {
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
        return {
            "instrument_date": "2025-06-20",
            "instrument_kind": "agreement_for_sale",
            "property_class": "residential",
            "consideration": "6000000",
            "value": "6000000",
        }
    if tax_type == "stamp_stock":
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
    if tax_type == "stamp_lease":
        return {
            "instrument_date": "2025-06-20",
            "term_kind": "fixed",
            "term_start": "2025-07-01",
            "term_end": "2027-06-30",
            "rent_input_mode": "fixed_monthly",
            "monthly_rent": "10000",
            "premium": "0",
        }
    raise AssertionError(tax_type)


def _envelope(tax_type: str) -> dict:
    return {
        "tax_type": tax_type,
        "schema_version": "1.0.0",
        "input": _facts_for(tax_type),
    }


def _initial_content(ai) -> dict:
    binding = ai.store.current()
    return ai.store.get_bundle(binding["bundle_id"])


def _publish(ai, content: dict) -> dict:
    from app.config import ENGINE_VERSION

    out = ai.store.publish(content, evidence_refs=[])
    return {**out, "engine_version": ENGINE_VERSION}


def _fresh_if_match(api) -> dict[str, str]:
    payload = api.get("/api/v1/meta/version").json()
    return {
        "If-Match": (
            f'"edit-{int(payload["session_epoch"])}-{int(payload["input_revision"])}"'
        )
    }


def _seed_confirmation(ai, tax_type: str, binding: dict, canonical: dict) -> str:
    from app.config import PREPARE_TTL_SECONDS
    from app.core.hashing import canonical_json_bytes

    sid = ai.session_id()
    registry = ai.registry
    input_bytes = canonical_json_bytes(canonical)
    input_hash = "sha256:" + hashlib.sha256(input_bytes).hexdigest()
    prepared_id = f"p-gate-{tax_type}"
    revision = registry.current_input_revision(sid)
    prepared = {
        "prepared_id": prepared_id,
        "tax_type": tax_type,
        "schema_version": "1.0.0",
        "input": copy.deepcopy(canonical),
        "input_hash": input_hash,
        "input_revision": revision,
        "binding": copy.deepcopy(binding),
        "session_epoch": registry.session_epoch,
        "execute_target": EXECUTE_TARGETS[tax_type],
        "created_at": time.monotonic(),
        "ttl": PREPARE_TTL_SECONDS,
    }
    assert registry.save_prepared_if_current(
        sid, prepared, registry.session_epoch, revision
    ), "前置：prepared 保存失败"
    confirmation = registry.issue_confirmation(
        sid, prepared_id, input_hash, binding, store=ai.store
    )
    return confirmation["confirmation_id"]


def test_five_tax_fixed_profile_content_anchor_all_guard_entries(ai) -> None:
    """五税逐个：改 rule＋同步 bundle hash → prepare 422、execute 422、
    AI calculate rejected、AI precheck rejected、admit_runtime 拒绝；无金额。"""
    from app.api.validation import validate_prepare_body
    from app.core.errors import AppError
    from app.rules import runtime as rules_runtime
    from app.rules.admission import FIXED_PROFILE_ID_BY_TAX

    api = ai.api
    base = copy.deepcopy(_initial_content(ai))
    sid = ai.session_id()
    records_before = frozenset(ai.registry._sessions[sid]["records"].keys())

    problems: list[str] = []
    for tax_type in EXECUTE_TARGETS:
        expected_id = FIXED_PROFILE_ID_BY_TAX[tax_type]
        content = copy.deepcopy(base)
        target = next(
            profile
            for profile in content["frozen_semantics"]["profiles"]
            if profile.get("profile_id") == expected_id
        )
        target["rule"] = str(target.get("rule") or "") + "（tampered-rule）"
        binding = _publish(ai, content)

        # 1) prepare admission
        resp = api.post(
            PREPARE_PATH,
            json=_envelope(tax_type),
            headers=_fresh_if_match(api),
        )
        if resp.status_code != 422:
            problems.append(
                f"{tax_type}/prepare: 须 422，实际 {resp.status_code}: "
                f"{resp.text[:200]!r}"
            )
        elif (resp.json().get("error") or {}).get("code") != (
            "E_RULES_STATE_UNVERIFIABLE"
        ):
            problems.append(f"{tax_type}/prepare: 错误码不符")
        if "prepared_id" in (resp.json() if resp.status_code != 422 else {}):
            problems.append(f"{tax_type}/prepare: 不得返回 prepared_id")

        # 2) 构造与篡改束绑定的 prepared/confirmation，验证 execute 实际入口
        _, canonical = validate_prepare_body(_envelope(tax_type))
        confirmation_id = _seed_confirmation(ai, tax_type, binding, canonical)

        exec_resp = api.post(
            EXECUTE_TARGETS[tax_type], json={"confirmation_id": confirmation_id}
        )
        if exec_resp.status_code != 422:
            problems.append(
                f"{tax_type}/execute: 须 422，实际 {exec_resp.status_code}: "
                f"{exec_resp.text[:200]!r}"
            )
        exec_payload = exec_resp.json()
        if (exec_payload.get("error") or {}).get("code") != (
            "E_RULES_STATE_UNVERIFIABLE"
        ):
            problems.append(f"{tax_type}/execute: 错误码不符")
        if "amounts" in exec_payload or "status" in exec_payload:
            problems.append(f"{tax_type}/execute: 拒绝不得返回金额/状态")

        # 3) AI calculate（同一 confirmation；execute 拒绝未消费）
        calc_resp, _ = ai_round(
            api,
            ai.outbound,
            MSG,
            wire_tool_calls_body(
                [
                    {
                        "id": f"call_calc_{tax_type}",
                        "name": "calculate_confirmed",
                        "arguments": {"confirmation_id": confirmation_id},
                    }
                ]
            ),
        )
        if calc_resp.status_code != 200:
            problems.append(f"{tax_type}/ai-calc: 工具轮须 200")
        else:
            entry = tool_results_of(calc_resp.json())[0]
            if entry["outcome"] != "rejected":
                problems.append(f"{tax_type}/ai-calc: 须 rejected，实际 {entry!r}")
            elif (entry.get("error") or {}).get("code") != (
                "E_RULES_STATE_UNVERIFIABLE"
            ):
                problems.append(f"{tax_type}/ai-calc: 错误码不符")
            if entry.get("result") is not None:
                problems.append(f"{tax_type}/ai-calc: rejected 不得有 result")
            if "amounts" in calc_resp.json():
                problems.append(f"{tax_type}/ai-calc: 不得返回金额")

        # 4) AI precheck（完整信封）
        pre_resp, _ = ai_round(
            api,
            ai.outbound,
            MSG,
            wire_tool_calls_body(
                [
                    {
                        "id": f"call_pre_{tax_type}",
                        "name": "propose_input",
                        "arguments": {"input": _envelope(tax_type)},
                    }
                ]
            ),
        )
        if pre_resp.status_code != 200:
            problems.append(f"{tax_type}/ai-precheck: 工具轮须 200")
        else:
            entry = tool_results_of(pre_resp.json())[0]
            if entry["outcome"] != "rejected":
                problems.append(
                    f"{tax_type}/ai-precheck: 须 rejected，实际 {entry!r}"
                )
            elif (entry.get("error") or {}).get("code") != (
                "E_RULES_STATE_UNVERIFIABLE"
            ):
                problems.append(f"{tax_type}/ai-precheck: 错误码不符")

        # 5) runtime admission 直接入口
        guard_read = rules_runtime.read_guard(
            ai.app.state.update_scheduler, tax_type=tax_type, facts=canonical
        )
        try:
            rules_runtime.admit_runtime(
                binding,
                content,
                tax_type=tax_type,
                facts=canonical,
                guard_read=guard_read,
            )
        except AppError as exc:
            if exc.code != "E_RULES_STATE_UNVERIFIABLE":
                problems.append(f"{tax_type}/runtime: 错误码 {exc.code}")
        else:
            problems.append(f"{tax_type}/runtime: 篡改 profile 未拒绝")

        if ai.registry._sessions[sid]["records"].keys() != records_before:
            problems.append(f"{tax_type}: 任一拒绝路径不得写记录")
            records_before = frozenset(
                ai.registry._sessions[sid]["records"].keys()
            )

    assert not problems, "五税固定 profile 全入口缺口：\n  - " + "\n  - ".join(
        problems
    )
