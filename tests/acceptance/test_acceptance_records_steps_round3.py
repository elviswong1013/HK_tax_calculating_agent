"""REQ-13 各引擎计算记录 steps 非空且直读引擎输出（第二轮 REJECTED 缺口 Red）。

REQ: REQ-13（record 全字段：步骤（税率、扣除、暂缴、宽减）——各税项实际
计算步骤，金额直读引擎 canonical 分项，不得编造）。
验收发现锚点（终验第二轮 REJECTED）:
  - app/web/records.build_steps：tax_type != "salaries_tax" 一律返回 []
    ——利得税/物业税/个人入息课税/三类印花税的 complete 记录 steps 恒空；
  - app/web/records.build_core：status != "complete" 时 steps 恒 []——
    薪俸税 partial 记录（缺已缴暂缴）也不给已算部分的步骤。
规格锚点:
  - SDD §6.4/Annex A §6.6：计算步骤＝计税基数、适用税率、扣除/免税额、
    取整及中间结果；依据＝确认事实＋规则束数值，金额直读引擎 canonical
    分项（不编造）。
  - Annex C C3.3/C3.4：partial＝部分结果——已算组件照常呈现。
期望值来源: 结构/文本断言（steps 非空、含引擎实际输出金额、说明税率与
  取整口径）；金额期望仅「engine amounts 值出现在步骤文本」，无独立数值。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - build_steps 覆盖全部七个执行目标税项（各引擎按自身口径：基数/税率/
    扣除或免税额/取整/结果），partial 记录对已算组件同样给出步骤；
    步骤文本中的金额一律取自引擎 canonical amounts 输出。
"""

from __future__ import annotations

from _acc_helpers import (
    TAX_EXECUTE_TARGETS,
    confirm,
    execute,
    minimal_facts,
    prepare,
    salaries_min_facts,
)

# 全部非薪俸税执行目标（complete 记录须有步骤）
_NON_SALARIES_TARGETS = tuple(
    (tax_type, target)
    for tax_type, target in TAX_EXECUTE_TARGETS.items()
    if tax_type != "salaries_tax"
)


def _drive(api, tax_type: str, facts: dict, target: str) -> dict:
    r_prepare = prepare(api, tax_type, facts)
    assert r_prepare.status_code == 201, (
        f"{tax_type}: prepare 须 201（最小合法事实），实际 "
        f"{r_prepare.status_code}: {r_prepare.text[:200]}"
    )
    r_confirm = confirm(api, r_prepare.json())
    assert r_confirm.status_code == 200, r_confirm.text[:200]
    r_execute = execute(api, r_confirm.json()["confirmation_id"], target)
    assert r_execute.status_code == 200, (
        f"{tax_type}: execute 须 200，实际 {r_execute.status_code}: "
        f"{r_execute.text[:200]}"
    )
    return r_execute.json()


def _check_steps(label: str, payload: dict, problems: list[str]) -> None:
    steps = payload.get("steps") or []
    if not isinstance(steps, list) or not steps:
        problems.append(
            f"{label}: steps 不得为空（REQ-13：各引擎实际计算步骤——基数/"
            "税率/扣除或免税额/取整/结果）"
        )
        return
    text = " ".join(str(step) for step in steps)
    amount_values = [
        value
        for value in (payload.get("amounts") or {}).values()
        if isinstance(value, str) and value
    ]
    if not any(value in text for value in amount_values):
        problems.append(
            f"{label}: steps 须引用引擎实际输出的 canonical 金额（不得编造），"
            f"engine amounts={amount_values!r}"
        )
    if "率" not in text:
        problems.append(f"{label}: steps 须说明适用税率/费率（§6.4）")
    if "取整" not in text:
        problems.append(f"{label}: steps 须说明取整口径（§6.4）")


def test_acceptance_records_steps_nonempty_all_engines(make_acc) -> None:
    """薪俸税 partial 记录与全部非薪俸税 complete 记录的 steps 非空，且
    步骤文本含引擎实际输出金额、说明税率与取整口径。"""
    acc = make_acc("steps3")
    problems: list[str] = []

    # —— 薪俸税 partial（缺已缴暂缴）：已算组件的步骤照常呈现 ——
    salaries_partial = _drive(
        acc.api,
        "salaries_tax",
        salaries_min_facts(),  # 不带 provisional_paid → partial（REQ-8）
        TAX_EXECUTE_TARGETS["salaries_tax"],
    )
    if salaries_partial.get("status") != "partial":
        problems.append(
            "前置自证失败：缺 provisional_paid 的薪俸事实应 partial，实际 "
            f"{salaries_partial.get('status')!r}"
        )
    else:
        _check_steps("salaries_tax(partial)", salaries_partial, problems)

    # —— 六个非薪俸税执行目标：complete 记录步骤非空 ——
    for tax_type, target in _NON_SALARIES_TARGETS:
        payload = _drive(acc.api, tax_type, minimal_facts(tax_type), target)
        if payload.get("status") != "complete":
            problems.append(
                f"{tax_type}: 最小合法事实应 complete（前置自证），实际 "
                f"{payload.get('status')!r}"
            )
            continue
        _check_steps(tax_type, payload, problems)

    assert not problems, (
        "REQ-13 缺口：各引擎记录 steps 缺失或未直读引擎输出：\n  - "
        + "\n  - ".join(problems)
    )
