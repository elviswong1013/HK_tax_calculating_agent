"""REQ-10 输入形状结构化拒绝与零金额 unknown 追问（终验第二轮 REJECTED 缺口 Red）。

REQ: REQ-10（三态输入语义：unknown 不得被静默吞掉；错误类型输入 → 统一
错误形状结构化拒绝）＋REQ-3（扣除逐项枚举资格语义）。
验收发现锚点（终验第二轮 REJECTED）:
  - app/api/validation.py.validate_prepare_body：tax_type 直接作
    _TAX_VALIDATORS.get(tax_type) 的键——列表/对象等不可哈希类型引发
    未处理 TypeError（进程内 ASGI 下异常直接冒出，部署形态＝500），
    而非 C3.7 统一错误形状的 422 E_INPUT_TYPE；
  - app/api/validation.py._validate_deductions：不支持 item_type 的分支只
    检查正数申索，eligibility 键被静默丢弃——voluntary_mpf_contribution
    amount="0" 且 eligibility=unknown 时既不追问也不拒（unknown 被零金额
    吞掉，三态被压缩）；引擎侧同样不看 eligibility。
规格锚点:
  - Annex C C3.7：E_INPUT_TYPE → 422；错误响应统一形状
    {"error": {code, message_zh, field, ...}}——任何非法输入（含错误
    JSON 类型）都走结构化错误，不得未处理异常。
  - SDD §3 三态：unknown ≠ false ≠ 缺失——显式 eligibility=unknown 必须
    追问（E_ELIGIBILITY_UNKNOWN＋questions 或 needs_input），不得因金额
    为零而免问；显式 false ＝明确不申索、合法跳过。
  - 自愿性 MPF 供款不在任何法定扣除附表内（仅附表 3B 强制性供款可扣）
    ＝不支持扣除示例；其 amount="0" 无效果（合法）。
期望值来源: 结构/状态断言（422＋错误码、needs_input＋questions、
  complete 无追问）；无金额数值期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - validate_prepare_body 对非字符串 tax_type（列表/对象/数字/布尔/null/
    空串）一律结构化 422 E_INPUT_TYPE（field=tax_type），不得让不可哈希
    类型触达 dict.get。
  - 不支持扣除 item_type 的 eligibility 同样参与三态校验：显式
    value="unknown" → E_ELIGIBILITY_UNKNOWN＋questions（prepare）或引擎
    needs_input＋questions（金额零也不例外）。
"""

from __future__ import annotations

from _acc_helpers import (
    PREPARE_PATH,
    confirm,
    error_code,
    execute,
    prepare,
    salaries_min_facts,
)
from _acc_helpers import TAX_EXECUTE_TARGETS as _TARGETS

_SALARIES_TARGET = _TARGETS["salaries_tax"]

# 错误类型变体（REQ-10：全部须结构化 422 E_INPUT_TYPE，非未处理异常）
WRONG_SHAPES: tuple[tuple[str, object], ...] = (
    ("列表", []),
    ("对象", {}),
    ("数字", 12345),
    ("布尔", True),
    ("null", None),
    ("空字符串", ""),
)


def test_acceptance_tax_type_wrong_shape_structured_rejection(api) -> None:
    """tax_type 各错误类型（列表/对象/数字/布尔/null/空串）→ 422 结构化
    E_INPUT_TYPE（统一错误形状），不得未处理 TypeError（500 等价）。"""
    problems: list[str] = []
    for label, bad_tax_type in WRONG_SHAPES:
        body = {
            "tax_type": bad_tax_type,
            "schema_version": "1.0.0",
            "input": salaries_min_facts(with_provisional_zero=True),
        }
        try:
            response = api.post(PREPARE_PATH, json=body)
        except Exception as exc:  # 未处理异常冒出＝500 等价（非结构化拒绝）
            problems.append(
                f"tax_type={label!r}：未处理异常 {type(exc).__name__}: {exc}——"
                "错误类型必须走统一错误形状 422 E_INPUT_TYPE（C3.7），"
                "不得让不可哈希类型触达查找表"
            )
            continue
        if response.status_code != 422:
            problems.append(
                f"tax_type={label!r}：须 422（C3.7），实际 {response.status_code}: "
                f"{response.text[:200]!r}"
            )
            continue
        code = error_code(response)
        if code != "E_INPUT_TYPE":
            problems.append(
                f"tax_type={label!r}：错误码须 E_INPUT_TYPE，实际 {code!r}: "
                f"{response.text[:200]!r}"
            )
    assert not problems, (
        "REQ-10 缺口：tax_type 错误类型输入未结构化拒绝：\n  - "
        + "\n  - ".join(problems)
    )


def test_acceptance_zero_amount_unknown_eligibility_still_questions(api) -> None:
    """voluntary_mpf_contribution amount="0"：
    - eligibility=unknown → 必须追问（prepare E_ELIGIBILITY_UNKNOWN＋questions
      或 execute needs_input＋questions）——unknown 不得被零金额吞掉；
    - eligibility=false → 合法跳过（complete、无追问、金额与无扣除基线一致）。"""
    # —— 基线：无 deduction_items（对照金额）——
    r_base = prepare(api, "salaries_tax", salaries_min_facts(with_provisional_zero=True))
    assert r_base.status_code == 201, r_base.text[:200]
    r_base_confirm = confirm(api, r_base.json())
    assert r_base_confirm.status_code == 200, r_base_confirm.text[:200]
    r_base_exec = execute(
        api, r_base_confirm.json()["confirmation_id"], _SALARIES_TARGET
    )
    assert r_base_exec.status_code == 200, r_base_exec.text[:200]
    baseline_before = r_base_exec.json()["amounts"]["before_reduction"]

    # —— (1) amount="0" + eligibility=unknown：追问不得被零金额吞掉 ——
    facts_unknown = salaries_min_facts(with_provisional_zero=True)
    facts_unknown["deduction_items"] = [
        {
            "item_type": "voluntary_mpf_contribution",
            "amount": "0",
            "eligibility": {"value": "unknown"},
        }
    ]
    r_prepare = prepare(api, "salaries_tax", facts_unknown)
    if r_prepare.status_code == 201:
        r_confirm = confirm(api, r_prepare.json())
        assert r_confirm.status_code == 200, r_confirm.text[:200]
        r_execute = execute(
            api, r_confirm.json()["confirmation_id"], _SALARIES_TARGET
        )
        assert r_execute.status_code == 200, r_execute.text[:200]
        payload = r_execute.json()
        asked = (
            payload.get("status") == "needs_input"
            and bool(payload.get("questions"))
            and all(value is None for value in (payload.get("amounts") or {}).values())
        )
        assert asked, (
            "REQ-10：显式 eligibility=unknown 必须追问（needs_input＋questions、"
            "金额一律 null）——不得因 amount=\"0\" 静默吞掉 unknown；实际 "
            f"status={payload.get('status')!r} questions={payload.get('questions')!r}"
            f" amounts={payload.get('amounts')!r}"
        )
    else:
        rejected_at_prepare = (
            r_prepare.status_code == 422
            and error_code(r_prepare) == "E_ELIGIBILITY_UNKNOWN"
            and bool(r_prepare.json().get("questions"))
        )
        assert rejected_at_prepare, (
            "REQ-10：eligibility=unknown 的追问可在 prepare 以 "
            "E_ELIGIBILITY_UNKNOWN＋questions 拒绝，或在 execute 以 "
            f"needs_input 追问；实际 prepare={r_prepare.status_code} "
            f"{error_code(r_prepare)!r}: {r_prepare.text[:200]!r}"
        )

    # —— (2) amount="0" + eligibility=false：明确不申索 → 合法跳过 ——
    facts_false = salaries_min_facts(with_provisional_zero=True)
    facts_false["deduction_items"] = [
        {
            "item_type": "voluntary_mpf_contribution",
            "amount": "0",
            "eligibility": {"value": False},
        }
    ]
    r2_prepare = prepare(api, "salaries_tax", facts_false)
    assert r2_prepare.status_code == 201, (
        f"显式 eligibility=false 的零金额不支持扣除项是合法事实（跳过），"
        f"不得拒收；实际 {r2_prepare.status_code}: {r2_prepare.text[:200]}"
    )
    r2_confirm = confirm(api, r2_prepare.json())
    assert r2_confirm.status_code == 200, r2_confirm.text[:200]
    r2_execute = execute(api, r2_confirm.json()["confirmation_id"], _SALARIES_TARGET)
    assert r2_execute.status_code == 200, r2_execute.text[:200]
    payload2 = r2_execute.json()
    assert payload2.get("status") == "complete", (
        f"eligibility=false → 明确不申索、合法跳过（complete），"
        f"实际 {payload2.get('status')!r}"
    )
    assert not payload2.get("questions"), (
        f"明确不申索不得追问：questions={payload2.get('questions')!r}"
    )
    assert payload2["amounts"]["before_reduction"] == baseline_before, (
        "eligibility=false 且 amount=\"0\" → 无扣除效果，金额须与无扣除"
        f"基线一致：{payload2['amounts']!r} vs 基线 {baseline_before!r}"
    )
