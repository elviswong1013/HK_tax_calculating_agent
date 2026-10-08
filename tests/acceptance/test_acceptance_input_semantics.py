"""REQ-3/8/10 输入三态与扣除拒算语义（终验 REJECTED 缺口 Red）。

REQ: REQ-8（缺 PST 组件仅部分结果且 balance null；最终税/宽减/已缴暂缴/
下年暂缴/结欠退款分列）＋REQ-10（三态输入：缺失≠显式零≠unknown；不支持
扣除为正或 unknown → 拒算受影响税项）＋REQ-3（扣除逐项枚举资格语义）。
验收发现锚点（终验 REJECTED）:
  - app/engines/salaries.py._provisional_paid：provisional_paid 缺失被静默
    按 0 计算（status=complete、balance=final−0）——REQ-8 要求缺失 →
    partial、provisional_paid/balance=null、missing_components 标注；
  - app/api/validation.py：provisional_paid 根本不在薪俸输入 schema 内，
    显式 "0"（合法事实）被 E_INPUT_EXTRA 拒绝——三态被压缩为两态；
  - app/api/validation.py._validate_deductions：item_type 任意值均放行、
    eligibility 键被静默丢弃；引擎对 deduction_items 完全不看——不支持
    扣除正数既不 E_DEDUCTION_UNSUPPORTED、unknown eligibility 也不追问。
规格锚点:
  - SDD §3 三态；REQ-10「不支持扣除字段为正数或 unknown → 拒算受影响税项」；
    Annex C C3.5 薪俸族 deduction_items[]{item_type,amount,eligibility}
    （逐项枚举，无 catch-all）；附表 3C 长者住宿照顾开支＝T1（Annex D D7.0，
    eligibility unknown 的合法在册扣除示例）；自愿性 MPF 供款不在任何法定
    扣除附表内（仅附表 3B 强制性供款可扣）＝不支持扣除示例。
  - Annex C C3.7：E_DEDUCTION_UNSUPPORTED → 422；E_ELIGIBILITY_UNKNOWN →
    422＋questions[]。
期望值来源: 结构/状态断言（partial/complete、None/非 None、错误码、
  questions）；无金额数值期望。
"""

from __future__ import annotations

from _acc_helpers import (
    TAX_EXECUTE_TARGETS,
    confirm,
    error_code,
    execute,
    prepare,
    salaries_min_facts,
)

_SALARIES_TARGET = TAX_EXECUTE_TARGETS["salaries_tax"]


def _drive(api, facts: dict):
    """三段驱动；返回 (prepare_response, execute_response|None)。"""
    r_prepare = prepare(api, "salaries_tax", facts)
    if r_prepare.status_code != 201:
        return r_prepare, None
    r_confirm = confirm(api, r_prepare.json())
    assert r_confirm.status_code == 200, (
        f"confirm 须 200（C3.2.2），实际 {r_confirm.status_code}: {r_confirm.text[:200]}"
    )
    return r_prepare, execute(
        api, r_confirm.json()["confirmation_id"], _SALARIES_TARGET
    )


def test_acceptance_missing_pst_is_partial_not_zero(api) -> None:
    """薪俸 240000/MPF 9000 未提供 provisional_paid → partial＋
    provisional_paid/balance=null＋missing_components 含 provisional_paid；
    显式 "0" → 合法 complete（缺失≠显式零）。"""
    # —— 缺失（键不存在 ≠ 显式零，SDD §3）——
    r_prepare, r_execute = _drive(api, salaries_min_facts())
    assert r_prepare.status_code == 201, (
        f"缺失 provisional_paid 的最小事实应可 prepare（缺口走 partial 而非拒收），"
        f"实际 {r_prepare.status_code}: {r_prepare.text[:200]}"
    )
    assert r_execute is not None and r_execute.status_code == 200, (
        f"execute 须 200，实际 {getattr(r_execute, 'status_code', None)}: "
        f"{getattr(r_execute, 'text', '')[:200]}"
    )
    payload = r_execute.json()
    assert payload["status"] == "partial", (
        "未提供 provisional_paid（已缴暂缴）时必须 partial——缺失≠显式零，"
        f"不得静默按 0 计算；实际 status={payload['status']!r}，"
        f"amounts={payload['amounts']!r}"
    )
    amounts = payload["amounts"]
    assert amounts["provisional_paid"] is None, (
        f"缺 PST 组件 → provisional_paid 必须 null（REQ-8），实际 {amounts!r}"
    )
    assert amounts["balance"] is None, (
        f"缺 PST 组件 → balance 必须 null（无已缴暂缴则无结欠/退税，REQ-8），"
        f"实际 {amounts!r}"
    )
    assert amounts["final_after_reduction"] is not None, (
        f"partial 仍须给出已知最终部分（REQ-8 仅缺组件为 null），实际 {amounts!r}"
    )
    missing = payload.get("missing_components") or []
    assert any("provisional_paid" in str(item) for item in missing), (
        f"missing_components 必须标注 provisional_paid（REQ-8），实际 {missing!r}"
    )

    # —— 显式 "0"（合法事实 → complete）——
    r2_prepare, r2_execute = _drive(
        api, salaries_min_facts(with_provisional_zero=True)
    )
    assert r2_prepare.status_code == 201, (
        "显式 provisional_paid=\"0\" 是合法事实（三态：显式零≠缺失），"
        f"不得 E_INPUT_EXTRA 拒收；实际 {r2_prepare.status_code}: "
        f"{r2_prepare.text[:200]}"
    )
    assert r2_execute is not None and r2_execute.status_code == 200, (
        f"显式 \"0\" execute 须 200，实际 {r2_execute.status_code}: "
        f"{r2_execute.text[:200]}"
    )
    payload2 = r2_execute.json()
    assert payload2["status"] == "complete", (
        f"显式 \"0\" 时组件齐全 → complete，实际 {payload2['status']!r}"
    )
    assert payload2["amounts"]["provisional_paid"] == "0", payload2["amounts"]
    assert payload2["amounts"]["balance"] is not None, payload2["amounts"]


def test_acceptance_unsupported_deduction_positive_rejected(api) -> None:
    """不支持扣除 item_type 正数 → E_DEDUCTION_UNSUPPORTED 拒算（prepare 或
    execute 任一段拒均可，但不得静默忽略）；在册扣除 eligibility unknown →
    needs_input 追问（不得静默丢弃）。"""
    # —— (1) 不支持扣除正数：自愿性 MPF 供款（仅附表 3B 强制性供款可扣）——
    facts = salaries_min_facts()
    facts["deduction_items"] = [
        {"item_type": "voluntary_mpf_contribution", "amount": "5000"}
    ]
    r_prepare = prepare(api, "salaries_tax", facts)
    if r_prepare.status_code == 201:
        # prepare 放行 → 执行段必须拒算（不得静默忽略后给 complete）
        r_confirm = confirm(api, r_prepare.json())
        assert r_confirm.status_code == 200, r_confirm.text[:200]
        r_execute = execute(
            api, r_confirm.json()["confirmation_id"], _SALARIES_TARGET
        )
        rejected = (
            r_execute.status_code != 200
            and error_code(r_execute) == "E_DEDUCTION_UNSUPPORTED"
        )
        blocked_record = (
            r_execute.status_code == 200
            and r_execute.json().get("status") == "blocked"
        )
        assert rejected or blocked_record, (
            "不支持扣除（voluntary_mpf_contribution 正数）必须以 "
            "E_DEDUCTION_UNSUPPORTED 拒算受影响税项（REQ-10），不得静默忽略："
            f"execute={r_execute.status_code} status="
            f"{r_execute.json().get('status') if r_execute.status_code == 200 else None!r} "
            f"{r_execute.text[:200]!r}"
        )
    else:
        assert r_prepare.status_code == 422, (
            f"拒算须 422（C3.7），实际 {r_prepare.status_code}: "
            f"{r_prepare.text[:200]}"
        )
        assert error_code(r_prepare) == "E_DEDUCTION_UNSUPPORTED", (
            f"不支持扣除正数 → E_DEDUCTION_UNSUPPORTED，实际 "
            f"{error_code(r_prepare)!r}: {r_prepare.text[:200]}"
        )

    # —— (2) 在册扣除 eligibility unknown：长者住宿照顾开支（附表 3C，T1）——
    facts2 = salaries_min_facts()
    facts2["deduction_items"] = [
        {
            "item_type": "elder_residential_care",
            "amount": "40000",
            "eligibility": {"value": "unknown"},
        }
    ]
    r2_prepare = prepare(api, "salaries_tax", facts2)
    asked_at_prepare = (
        r2_prepare.status_code == 422
        and error_code(r2_prepare) == "E_ELIGIBILITY_UNKNOWN"
    )
    if r2_prepare.status_code == 201:
        r2_confirm = confirm(api, r2_prepare.json())
        assert r2_confirm.status_code == 200, r2_confirm.text[:200]
        r2_execute = execute(
            api, r2_confirm.json()["confirmation_id"], _SALARIES_TARGET
        )
        asked_at_execute = (
            r2_execute.status_code == 200
            and r2_execute.json().get("status") == "needs_input"
            and r2_execute.json().get("questions")
        )
        assert asked_at_execute, (
            "在册扣除 eligibility unknown 必须 needs_input＋中文问题清单"
            "（REQ-10 追问，不得静默丢弃 eligibility），实际 execute="
            f"{r2_execute.status_code} {r2_execute.text[:200]!r}"
        )
    else:
        assert asked_at_prepare, (
            "在册扣除 eligibility unknown → E_ELIGIBILITY_UNKNOWN＋questions"
            f"（REQ-10），实际 {r2_prepare.status_code} "
            f"{error_code(r2_prepare)!r}: {r2_prepare.text[:200]}"
        )
        assert r2_prepare.json().get("questions"), (
            "追问响应必须携带非空 questions[]（REQ-10）"
        )
