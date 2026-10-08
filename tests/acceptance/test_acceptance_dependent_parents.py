"""REQ-3 受养父母免税额实际计算（终验第二轮 REJECTED 缺口 Red）。

REQ: REQ-3（SDD §9：受养人免税额逐项计入薪俸税；资格事实三态、缺事实
追问而非默认）。
验收发现锚点（终验第二轮 REJECTED）:
  - app/engines/salaries.py：引擎对 dependent_parents 完全不看——
    「受养父母/兄弟姊妹等首版不建模」——带受养父母申索的输入与不带
    的输入得到完全相同的税额（600,000 收入 2026/27 before_reduction
    同为 57820）；供养/居港事实未知也不追问。
  - app/api/validation.py._validate_parents：条目仅放行
    {relationship, birth_date, residence, claim_owner}——供养事实与
    全年同住事实（s.30(4)/s.30(3)(b) 的资格前提）根本不在 schema 内。
规格锚点:
  - Cap.112 s.30/30A（Annex D D7；台账 1.17 T1）：受养父母/祖父母须
    通常居于香港；60+（或伤残津贴资格）一档、55–59（通常居港＋不符合
    伤残津贴资格＋当年未满 60）一档，每名分别给；全年连续同住额外同额
    （s.30(3)(b)/(3A)(b)）；维持判据＝非足值同住 ≥连续 6 个月或年内
    金钱供养 ≥$12,000（s.30(4)）。
  - 附表 4（T1；台账 1.17 §8）：2023/24–2025/26 父母/祖父母 60+
    50,000＋同住 50,000、55–59 25,000＋25,000；2026/27+ 60+
    55,000＋55,000、55–59 27,500＋27,500。
  - Annex C C3.3/C3.5：资格类事实三态；缺资格事实 → needs_input＋中文
    questions（不默认、不静默按无申索计）。
  - 附表 2（T1）：累进余额段 17%——本测试收入 600,000 下新增免税额
    全部落在该段，税额效果＝免税额×17%（金额锚可精确验证）。
期望值来源: 附表 4（T1）免税额数值 × 附表 2 余额段 17% 的精确税额差；
  结构/状态断言（needs_input、金额不等）。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 薪俸输入 schema 的 dependent_parents[] 条目在 C3.5 既有
    {relationship, birth_date, residence, claim_owner} 基础上扩展两个
    三态字段 {value: true|false|"unknown"}：
      * maintained（s.30(4) 供养/维持判据事实）；
      * living_together_all_year（s.30(3)(b) 全年连续同住额外额事实）。
  - 引擎按附表 4 年度行计算受养父母免税额（按课税年度末年龄分档），
    全年同住额外同额计入免税额；residence/maintained 为 unknown 或
    maintained 缺失 → needs_input＋中文 questions（六金额一律 null）。
"""

from __future__ import annotations

from decimal import Decimal

from _acc_helpers import (
    confirm,
    error_code,
    execute,
    parent_entry,
    prepare,
    salaries_min_facts,
)
from _acc_helpers import TAX_EXECUTE_TARGETS as _TARGETS

_SALARIES_TARGET = _TARGETS["salaries_tax"]

# 附表 2 余额段 17%（T1）：600,000 收入下受养父母免税额增量全部落在该段，
# 税额效果＝免税额×17%——按年度×档位的附表 4（T1）金额锚。
_TOP_RATE = Decimal(17) / Decimal(100)

# (年度, 出生日期, 该年度档位基础免税额)——年龄按课税年度末（3-31）判定：
# 1950-06-01 出生 → 2026/2027-03-31 时 75/76 岁（60+ 档）；
# 1968-06-01 出生 → 2026/2027-03-31 时 57/58 岁（55–59 档）。
_TIER_CASES: tuple[tuple[str, str, str], ...] = (
    ("2026_27", "1950-06-01", "55000"),  # 60+（2026/27 附表 4：55,000）
    ("2025_26", "1950-06-01", "50000"),  # 60+（2025/26 附表 4：50,000）
    ("2025_26", "1968-06-01", "25000"),  # 55–59（2025/26 附表 4：25,000）
    ("2026_27", "1968-06-01", "27500"),  # 55–59（2026/27 附表 4：27,500）
)


def _effect(allowance: str) -> int:
    """免税额 → 精确税额效果（allowance×17%；全部整数额）。"""
    return int(Decimal(allowance) * _TOP_RATE)


def _facts(year: str, parents: list | None = None) -> dict:
    facts = salaries_min_facts(year=year, with_provisional_zero=True)
    facts["employment_income"] = "600000"  # NCI 全程落在 17% 余额段
    if parents is not None:
        facts["dependent_parents"] = parents
    return facts


def _before_reduction(api, facts: dict, problems: list[str], label: str) -> str | None:
    """三段驱动并返回 before_reduction（结构失败计入 problems）。"""
    r_prepare = prepare(api, "salaries_tax", facts)
    if r_prepare.status_code != 201:
        problems.append(
            f"{label}: prepare 须 201（受养父母三态事实字段须被接受，"
            f"见本文件【拟名】契约），实际 {r_prepare.status_code} "
            f"{error_code(r_prepare)!r}: {r_prepare.text[:200]!r}"
        )
        return None
    r_confirm = confirm(api, r_prepare.json())
    assert r_confirm.status_code == 200, r_confirm.text[:200]
    r_execute = execute(api, r_confirm.json()["confirmation_id"], _SALARIES_TARGET)
    assert r_execute.status_code == 200, r_execute.text[:200]
    payload = r_execute.json()
    if payload.get("status") != "complete":
        problems.append(
            f"{label}: 事实齐全的受养父母申索须 complete，实际 "
            f"{payload.get('status')!r} questions={payload.get('questions')!r}"
        )
        return None
    value = (payload.get("amounts") or {}).get("before_reduction")
    if value is None:
        problems.append(f"{label}: before_reduction 不得为 null")
    return value


def _expect_needs_input(api, facts: dict, problems: list[str], label: str) -> None:
    """缺供养/居港事实 → prepare E_ELIGIBILITY_UNKNOWN 或 execute
    needs_input＋questions（金额一律 null），不得静默 complete。"""
    r_prepare = prepare(api, "salaries_tax", facts)
    if r_prepare.status_code == 201:
        r_confirm = confirm(api, r_prepare.json())
        assert r_confirm.status_code == 200, r_confirm.text[:200]
        r_execute = execute(api, r_confirm.json()["confirmation_id"], _SALARIES_TARGET)
        assert r_execute.status_code == 200, r_execute.text[:200]
        payload = r_execute.json()
        asked = (
            payload.get("status") == "needs_input"
            and bool(payload.get("questions"))
            and all(
                value is None for value in (payload.get("amounts") or {}).values()
            )
        )
        if not asked:
            problems.append(
                f"{label}: 缺资格事实须 needs_input＋中文追问（六金额一律 "
                f"null，不默认按无申索计），实际 status={payload.get('status')!r}"
                f" questions={payload.get('questions')!r}"
                f" amounts={payload.get('amounts')!r}"
            )
    else:
        rejected = (
            r_prepare.status_code == 422
            and error_code(r_prepare) == "E_ELIGIBILITY_UNKNOWN"
            and bool(r_prepare.json().get("questions"))
        )
        if not rejected:
            problems.append(
                f"{label}: prepare 拒绝须 E_ELIGIBILITY_UNKNOWN＋questions，"
                f"实际 {r_prepare.status_code} {error_code(r_prepare)!r}: "
                f"{r_prepare.text[:200]!r}"
            )


def test_acceptance_dependent_parents_allowance_computed(api) -> None:
    """受养父母申索被实际计算：税额按附表 4 档位×年度精确下降（60+ 与
    55–59 两档、两个年度），全年同住额外同额；不得与无父母输入结果相同；
    缺供养/居港事实 → needs_input 追问。"""
    problems: list[str] = []

    # —— 各年度无父母基线（600,000／MPF 9,000／单身／显式已缴暂缴 0）——
    base_by_year: dict[str, str] = {}
    for year in sorted({case[0] for case in _TIER_CASES}):
        base = _before_reduction(api, _facts(year), problems, f"{year} 无父母基线")
        if base is not None:
            base_by_year[year] = base

    # —— 各档位：基础申索（非全年同住）＋全年同住额外同额 ——
    for year, birth, allowance in _TIER_CASES:
        base = base_by_year.get(year)
        if base is None:
            continue
        claim = _before_reduction(
            api,
            _facts(year, [parent_entry(birth, together=False)]),
            problems,
            f"{year} 出生 {birth} 基础申索（非同住）",
        )
        if claim is None:
            continue
        if claim == base:
            problems.append(
                f"{year} 出生 {birth}：受养父母申索不得与无父母输入结果相同"
                f"（附表 4 {allowance} 免税额未计入）：{claim!r}"
            )
            continue
        expected = _effect(allowance)
        if int(base) - int(claim) != expected:
            problems.append(
                f"{year} 出生 {birth}：基础免税额税额效果须＝附表 4 "
                f"{allowance}×17%＝{expected}，实际 {int(base) - int(claim)}"
            )
        together = _before_reduction(
            api,
            _facts(year, [parent_entry(birth)]),
            problems,
            f"{year} 出生 {birth} 全年同住",
        )
        if together is None:
            continue
        if int(claim) - int(together) != expected:
            problems.append(
                f"{year} 出生 {birth}：全年同住额外额须与基础额同额"
                f"（附表 4 {allowance}＋{allowance}），额外税额效果实际 "
                f"{int(claim) - int(together)}（期望 {expected}）"
            )

    # —— 缺资格事实：居港 unknown / 供养事实缺失 → needs_input 追问 ——
    _expect_needs_input(
        api,
        _facts("2026_27", [parent_entry("1950-06-01", residence="unknown")]),
        problems,
        "居港事实 unknown",
    )
    missing_maintained = parent_entry("1950-06-01")
    missing_maintained.pop("maintained")  # 供养事实缺失（≠显式值≠unknown）
    _expect_needs_input(
        api,
        _facts("2026_27", [missing_maintained]),
        problems,
        "供养事实缺失",
    )

    assert not problems, (
        "REQ-3 缺口：受养父母免税额未被实际计算/资格事实未被追问：\n  - "
        + "\n  - ".join(problems)
    )
