"""REQ-3 薪俸税引擎（M2 salaries 泳道；Annex D D7 为税法语义权威）。

REQ: REQ-3（SDD §9：两径择低＋MPF 强制供款扣除＋年度宽减＋资格未知追问）。
规格锚点:
  - s.13(1)/(2)（Annex D D7.0）：累进（对 NCI）与标准（对 NAI−Part 4A；
    2024/25 起首 $5,000,000 15%、余额 16%）两径取较小。
  - s.12B(1)/(2)：NCI＝NAI−Part 4A 扣除−Part 5 免税额；合并评税＝两人 NAI
    合计−两人 Part 4A 扣除−Part 5 免税额（单一 NCI；一份已婚免税额）。
  - 附表 3B：MPF 强制性供款扣除每人上限 $18,000（合并评税两人各自封顶聚合）。
  - 附表 43：宽减 2024/25 cap 1,500、2025/26 cap 3,000、2026/27 无条目；
    宽减仅作用于最终税，不适用暂缴。
  - 取整（Annex D D7 末段取整矩阵①，三年度官方计算器 observed）：两径先
    分别 floor 再比较、floor 后相等取累进、外层单次 floor；无级距内取整。
  - Annex C C2：金额全链路规范字符串（禁 float）；取整点＝精确分数 floor。
  - Annex C C3.3/C3.4：status ∈ {complete, partial, needs_input, blocked}；
    amounts 恰六键（canonical 字符串或 None）；资格 unknown → needs_input
    ＋中文 questions，六键一律显式 None（不填零、不部分计算）。

【拟名】calculate_salaries_tax(facts: Mapping, bundle: Mapping) -> dict：
纯函数；facts 字段名沿用 Annex C C3.5 薪俸税族（year_of_assessment／
employment_income／mpf_mandatory_contributions／married_status{value}／
spouse_employment_income／spouse_mpf_mandatory_contributions／
joint_assessment_elected{value}／dependent_children[]{birth_date,residence}／
dependent_parents[]{relationship,birth_date,residence,maintained{value},
living_together_all_year{value}}／provisional_paid）。

v1 范围注记（本泳道 Green 目标＝tests/engines/test_salaries_engine.py）:
- 免税额覆盖基本／已婚（合并评税）／子女（s.31 未成年＋通常居于香港判定，
  含 s.31(1A) 出生年额外与 s.31(1B) 出生次年额外；18 岁或以上须全日制就学
  等事实不在 C3.5 薪俸族 schema 内 → 追问，不静默按无子女计）／受养父母
  （s.30/30A＋附表 4 年度行：60+ 与 55–59 档、s.30(3)(b) 全年同住额外同额、
  s.30(4) 供养判据；居港/供养 unknown 或供养缺失 → 追问）。兄弟姊妹/单亲
  等其余字段首版不建模，待台账晋升后增量入数；婚姻状况缺失/unknown、合并
  评税选择 unknown 一律 needs_input（不默认、不推断）。
- deduction_items[]（C3.5 逐项枚举）：未支持 item_type 正数申索 →
  E_DEDUCTION_UNSUPPORTED 拒算（REQ-10，与 prepare 校验双保险）；在册扣除
  （附表 3C，bundle.deductions）资格 unknown/缺失 → 追问；确认申索 →
  min(金额, 附表上限) 计入 Part 4A 扣除（两径基数同减）。
- provisional_paid（已缴暂缴）三态：缺失 ≠ 显式零——缺失 → partial、
  provisional_paid/balance=null、missing_components 标注（REQ-8）；显式
  金额（含 "0"）→ complete 并计算结欠／退税。
- 暂缴（next_provisional）＝同一事实按下一年度规则估算（下一年度不在规则
  束时退回本年度规则），不计宽减（附表 43 宽减不适用暂缴）。
- 已婚而未选合并评税 → s.10(1) 默认分别评税，本函数按事实本人一侧以基本
  免税额计算（配偶侧归其自身评税，不在本事实集内）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any, Mapping

from app.core.errors import AppError
from app.core.money import floor_at_point, format_canonical, parse_amount

_AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)


def calculate_salaries_tax(facts: Mapping, bundle: Mapping) -> dict:
    """计算薪俸税（纯函数；注入不可变 RuleBundle，SDD §3）。"""
    data = bundle["data"]["salaries"]
    year = facts.get("year_of_assessment")
    allowances_by_year = data["allowances"]
    year_data = allowances_by_year.get(year) if isinstance(year, str) else None
    if year_data is None:
        return _blocked(
            f"课税年度 {year!r} 不在薪俸税规则束适用范围（E_PERIOD_NOT_SUPPORTED）"
        )
    start = int(year[:4])
    ya_start, ya_end = date(start, 4, 1), date(start + 1, 3, 31)

    income = _amount(facts, "employment_income")
    mpf = _amount(facts, "mpf_mandatory_contributions")

    structure, questions = _resolve_structure(facts, ya_end)
    parents = _resolve_parents(facts, ya_end, questions)
    deductions = _resolve_deductions(facts, data, year, questions)
    if questions:
        return _needs_input(questions)

    before = _assess(
        income, mpf, structure, data, year_data, ya_start, ya_end,
        deductions, parents,
    )
    reduction = _reduction(before, data, year)
    final = before - reduction
    # 已缴暂缴三态（REQ-8）：缺失 → partial、provisional_paid/balance=null；
    # 显式金额（含 "0"）→ complete 并计算结欠／退税（正＝欠款、负＝退税）。
    paid = _provisional_paid(facts)
    # 暂缴＝同一事实按下一年度规则估算；下一年度不在束内 → 退回本年度规则；
    # 附表 43 宽减不适用暂缴 → 不计宽减。
    next_year = f"{start + 1}_{(start + 2) % 100:02d}"
    next_year_data = allowances_by_year.get(next_year, year_data)
    next_provisional = _assess(
        income,
        mpf,
        structure,
        data,
        next_year_data,
        date(start + 1, 4, 1),
        date(start + 2, 3, 31),
        deductions,
        parents,
    )

    return {
        "status": "complete" if paid is not None else "partial",
        "amounts": {
            "before_reduction": _canonical(Fraction(before)),
            "reduction": _canonical(Fraction(reduction)),
            "final_after_reduction": _canonical(Fraction(final)),
            "provisional_paid": None if paid is None else _canonical(paid),
            "next_provisional": _canonical(Fraction(next_provisional)),
            "balance": None if paid is None else _canonical(Fraction(final) - paid),
        },
        "questions": [],
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [] if paid is not None else ["provisional_paid"],
    }


def _assess(
    income: Fraction,
    mpf: Fraction,
    structure: Mapping,
    data: Mapping,
    year_data: Mapping,
    ya_start: date,
    ya_end: date,
    deductions: Fraction = Fraction(0),
    parents: list[tuple[str, bool]] | None = None,
) -> int:
    """单一年度两径计税：先分别 floor 再择低（floor 后相等取累进）。

    标准径法定基数＝NAI−Part 4A（s.13(2)，联合＝双方合计）；累进径基数
    ＝NCI（s.12B；联合按 s.12B(2) 单一口径，扣一份已婚免税额）。
    MPF 扣除每人封顶（附表 3B）；在册扣除（附表 3C 等）同属 Part 4A、
    两径基数同减；受养父母免税额（附表 4，s.30/30A）同额计入两径；
    宽减不在此层（仅作用于最终税）。
    """
    cap = Fraction(int(data["mpf_deduction_cap"]))
    part4a = min(mpf, cap) + deductions
    gross = income
    if structure["joint"]:
        part4a += min(structure["spouse_mpf"], cap)
        gross = income + structure["spouse_income"]
    base_standard = gross - part4a
    allowance = int(year_data["married"] if structure["joint"] else year_data["basic"])
    allowance += _child_allowance_total(
        structure["children"], year_data, ya_start, ya_end
    )
    allowance += _parent_allowance_total(parents or [], year_data)
    nci = base_standard - allowance
    progressive = floor_at_point(_band_tax(max(nci, Fraction(0)), data["progressive_bands"]))
    standard = floor_at_point(
        _band_tax(max(base_standard, Fraction(0)), data["standard_rate_bands"])
    )
    return progressive if progressive <= standard else standard


def _band_tax(base: Fraction, bands: list) -> Fraction:
    """按累进/标准带精确计税（Fraction，全程无 float）；末段无 width＝余额段。"""
    tax = Fraction(0)
    remaining = base
    for band in bands:
        rate = Fraction(int(band["rate_bp"]), 10000)
        width = band.get("width")
        if width is None:
            tax += remaining * rate
            break
        portion = min(remaining, int(width))
        tax += portion * rate
        remaining -= portion
        if remaining <= 0:
            break
    return tax


def _reduction(before: int, data: Mapping, year: str) -> int:
    """附表 43 年度宽减：min(税额×率, cap)；缺项年度（2026/27）＝无宽减。"""
    entry = data["reductions"].get(year)
    if entry is None:
        return 0
    rate = Fraction(int(entry["percent_bp"]), 10000)
    cap = Fraction(int(entry["cap"]))
    return floor_at_point(min(Fraction(before) * rate, cap))


def _child_allowance_total(
    children: list[date],
    year_data: Mapping,
    ya_start: date,
    ya_end: date,
) -> int:
    """子女免税额（附表 4）：每名基础额＋s.31(1A) 出生年额外＋s.31(1B) 出生
    次年额外（仅 2026/27+ 行有键）；整组合计不超附表 4 组上限。"""
    if not children:
        return 0
    base = int(year_data["child"])
    birth_year_add = int(year_data["child_birth_year_additional"])
    next_year_add = int(year_data.get("child_following_year_additional", "0"))
    prev_start, prev_end = (
        date(ya_start.year - 1, 4, 1),
        date(ya_start.year, 3, 31),
    )
    total = 0
    for birth in children:
        amount = base
        if ya_start <= birth <= ya_end:
            amount += birth_year_add  # s.31(1A) 出生课税年度额外
        elif next_year_add and prev_start <= birth <= prev_end:
            amount += next_year_add  # s.31(1B) 出生次一课税年度额外
        total += amount
    return min(total, int(year_data["child_aggregate_cap"]))


def _resolve_parents(
    facts: Mapping, ya_end: date, questions: list[str]
) -> list[tuple[str, bool]]:
    """受养父母/祖父母申索资格解析（s.30/30A；金额＝附表 4 年度行）。

    - 居港（residence）或供养（maintained）事实 unknown / 供养缺失 →
      追问（C3.3/C3.5：不默认、不静默按无申索计）；
    - 显式 False（不居港／不供养）／年度结束后出生／年度末未满 55 →
      事实可判定、不构成申索（计 0，不追问）；
    - 年度末 60+ → "60_or_above" 档；55–59 → "55_to_59" 档；
    - living_together_all_year unknown → 追问（三态不得默认）；True →
      s.30(3)(b) 全年连续同住额外同额；False/缺失 → 仅基础额。
    返回 [(档位, 全年同住)]；金额按各年度行在 _parent_allowance_total 取值。
    """
    raw_parents = facts.get("dependent_parents")
    if not raw_parents:
        return []
    if not isinstance(raw_parents, list):
        raise AppError(
            "E_INPUT_TYPE", "dependent_parents 必须为列表", field="dependent_parents"
        )
    parents: list[tuple[str, bool]] = []
    for index, entry in enumerate(raw_parents):
        field = f"dependent_parents[{index}]"
        if not isinstance(entry, Mapping):
            raise AppError(
                "E_INPUT_TYPE", f"{field} 须为对象", field=field
            )
        birth_raw = entry.get("birth_date")
        if not isinstance(birth_raw, str):
            questions.append("请提供受养父母/祖父母的出生日期，以便判定免税额档位。")
            continue
        try:
            birth = date.fromisoformat(birth_raw)
        except ValueError:
            raise AppError(
                "E_INPUT_TYPE",
                f"受养父母/祖父母出生日期格式非法：{birth_raw!r}",
                field=field,
            ) from None
        residence = _tri_state(entry.get("residence"))
        if residence is None or residence == "unknown":
            questions.append(
                f"请确认出生日期为 {birth_raw} 的受养父母/祖父母在课税年度内"
                "是否通常居于香港（s.30/30A 资格前提）。"
            )
            continue
        maintained = _tri_state(entry.get("maintained"))
        if maintained is None or maintained == "unknown":
            questions.append(
                f"请确认是否维持出生日期为 {birth_raw} 的受养父母/祖父母的"
                "生活（s.30(4)：与其同住连续不少于 6 个月，或年内给予其"
                "不少于 12,000 港元供养款）。"
            )
            continue
        if residence is False or maintained is False or birth > ya_end:
            continue  # 事实可判定：不构成 s.30 申索（非「未知」，不追问）
        if not _under_age(birth, ya_end, 60):
            tier = "60_or_above"
        elif not _under_age(birth, ya_end, 55):
            tier = "55_to_59"
        else:
            continue  # 年度末未满 55：不在附表 4 父母档位（事实可判定）
        together = _tri_state(entry.get("living_together_all_year"))
        if together == "unknown":
            questions.append(
                f"请确认该课税年度是否全年连续与出生日期为 {birth_raw} 的"
                "受养父母/祖父母同住（s.30(3)(b) 全年同住额外免税额）。"
            )
            continue
        parents.append((tier, together is True))
    return parents


def _parent_allowance_total(
    parents: list[tuple[str, bool]], year_data: Mapping
) -> int:
    """受养父母免税额（附表 4 年度行）：每名档位额＋全年同住额外同额
    （s.30(3)(b)/(3A)(b)）；每名分别给（s.30(3)）。"""
    total = 0
    for tier, together in parents:
        key = (
            "parent_aged_60_or_above" if tier == "60_or_above"
            else "parent_aged_55_to_59"
        )
        amount = int(year_data[key])
        total += amount + (amount if together else 0)
    return total


def _resolve_structure(facts: Mapping, ya_end: date) -> tuple[dict, list[str]]:
    """解析资格类事实（婚姻/合并评税/子女）→ 计算结构与中文追问清单。"""
    questions: list[str] = []
    married_value = _tri_state(facts.get("married_status"))
    married: bool | None
    if married_value == "married":
        married = True
        joint = _joint_election(facts, questions)
    elif married_value == "single":
        married, joint = False, False
    else:  # unknown／缺失：s.29 已婚分支无法判定 → 追问（C3.3，不默认）
        married, joint = None, False
        questions.append(
            "请确认本课税年度的婚姻状况（已婚／单身），以便判定已婚人士免税额与评税方式。"
        )

    children: list[date] = []
    if married is not None:
        children = _resolve_children(facts, ya_end, questions)

    structure: dict = {
        "joint": joint,
        "children": children,
        "spouse_income": Fraction(0),
        "spouse_mpf": Fraction(0),
    }
    if joint:
        structure["spouse_income"] = _amount(facts, "spouse_employment_income")
        structure["spouse_mpf"] = _amount(facts, "spouse_mpf_mandatory_contributions")
    return structure, questions


def _joint_election(facts: Mapping, questions: list[str]) -> bool:
    """s.10(2) 合并评税选择：unknown → 追问；缺失/False → 默认分别评税。"""
    value = _tri_state(facts.get("joint_assessment_elected"))
    if value == "unknown":
        questions.append("请确认夫妻本课税年度是否选择合并评税（joint assessment）。")
        return False
    return value is True


def _resolve_children(
    facts: Mapping, ya_end: date, questions: list[str]
) -> list[date]:
    """子女免税额资格解析（s.31）：未婚＋受供养＋（<18／18–25 全日制就学／
    伤残）＋通常居于香港。C3.5 薪俸族仅含 birth_date/residence——未成年且
    居港者计额；18 岁或以上须追问就学/伤残事实（不静默按无子女计）。"""
    children: list[date] = []
    raw_children = facts.get("dependent_children")
    if not raw_children:
        return children
    if not isinstance(raw_children, list):
        raise AppError(
            "E_INPUT_TYPE", "dependent_children 必须为列表", field="dependent_children"
        )
    for child in raw_children:
        if not isinstance(child, Mapping):
            raise AppError(
                "E_INPUT_TYPE", "受养子女条目必须为对象", field="dependent_children"
            )
        birth_raw = child.get("birth_date")
        if not isinstance(birth_raw, str):
            questions.append("请提供受养子女的出生日期，以便判定子女免税额资格。")
            continue
        try:
            birth = date.fromisoformat(birth_raw)
        except ValueError:
            raise AppError(
                "E_INPUT_TYPE",
                f"受养子女出生日期格式非法：{birth_raw!r}",
                field="dependent_children",
            ) from None
        residence = child.get("residence")
        residence_value = (
            residence.get("value") if isinstance(residence, Mapping) else None
        )
        if residence_value in (None, "unknown"):
            questions.append(
                f"请确认出生日期为 {birth_raw} 的受养子女在课税年度末是否通常居于香港。"
            )
            continue
        if birth > ya_end:
            continue  # 年度结束后出生：本年度不构成 s.31(1) 供养事实（事实可判定）
        if not _under_age(birth, ya_end, 18):
            questions.append(
                f"请确认出生日期为 {birth_raw} 的受养子女在课税年度末的"
                "教育／资格状况（18 岁或以上须全日制就学或因伤残不能工作）。"
            )
            continue
        children.append(birth)
    return children


def _under_age(birth: date, on: date, years: int) -> bool:
    """on 当日是否未满 years 岁（2/29 出生按平年 2/28 计满岁日）。"""
    try:
        threshold = birth.replace(year=birth.year + years)
    except ValueError:  # 2/29 → 平年无此日
        threshold = birth.replace(year=birth.year + years, day=28)
    return on < threshold


def _provisional_paid(facts: Mapping) -> Fraction | None:
    """已缴暂缴薪俸税事实（三态，REQ-8）：缺失 → None（partial、无 balance）；
    显式金额（含 "0"）→ 精确分数。"""
    raw = facts.get("provisional_paid")
    if raw is None:
        return None
    return Fraction(Decimal(parse_amount(raw)))


def _resolve_deductions(
    facts: Mapping, data: Mapping, year: str, questions: list[str]
) -> Fraction:
    """deduction_items 逐项枚举（C3.5，无 catch-all；REQ-10 双保险第二层）。

    - 未支持 item_type 正数申索 → E_DEDUCTION_UNSUPPORTED（拒算受影响税项；
      prepare 校验漏放时引擎仍拒，不静默忽略）；
    - 未支持 item_type 显式 "0" → 合法、无效果；其 eligibility 显式
      unknown → 仍追问（零金额不得吞掉三态）；
    - 在册扣除（bundle.deductions，附表 3C）资格 value 未知/缺失 → 追问
      （unknown 不得默认可扣）；
    - 资格确认 true → min(金额, 该年度附表上限) 计入 Part 4A 扣除；false →
      明确不申索、无效果。
    """
    items = facts.get("deduction_items")
    if not items:
        return Fraction(0)
    if not isinstance(items, list):
        raise AppError(
            "E_INPUT_TYPE", "deduction_items 必须为列表", field="deduction_items"
        )
    registry = data.get("deductions") or {}
    total = Fraction(0)
    for index, item in enumerate(items):
        field = f"deduction_items[{index}]"
        if not isinstance(item, Mapping):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        item_type = item.get("item_type")
        if not isinstance(item_type, str) or not item_type:
            raise AppError(
                "E_INPUT_TYPE", f"{field}.item_type 须为非空字符串", field=field
            )
        raw_amount = item.get("amount")
        if raw_amount is None:
            raise AppError("E_INPUT_MISSING", f"{field} 缺少 amount", field=field)
        amount = Fraction(Decimal(parse_amount(raw_amount)))
        rule = registry.get(item_type)
        if rule is None:
            if amount > 0:
                raise AppError(
                    "E_DEDUCTION_UNSUPPORTED",
                    f"扣除项 {item_type} 不在薪俸税可扣除附表内（逐项枚举，无 "
                    "catch-all）；正数申索须拒算受影响税项（REQ-10）。",
                    field=field,
                )
            # 显式 "0"：合法事实、无效果；eligibility 显式 unknown 仍追问。
            raw_eligibility = item.get("eligibility")
            unknown = isinstance(raw_eligibility, Mapping) and (
                raw_eligibility.get("value") == "unknown"
            )
            if unknown:
                questions.append(
                    f"扣除项 {item_type} 不在薪俸税可扣除附表内，而其资格状态"
                    "为「未知」：请确认是否申索该项（unknown 不得因金额为零"
                    "被静默吞掉）。"
                )
            continue
        eligibility = item.get("eligibility")
        value = eligibility.get("value") if isinstance(eligibility, Mapping) else None
        if value is True:
            cap_text = (rule.get("caps") or {}).get(year)
            if cap_text is None:
                raise AppError(
                    "E_RULES_STATE_UNVERIFIABLE",
                    f"规则束缺少 {item_type} 在 {year} 的扣除上限（附表条目）；拒算。",
                    field=field,
                )
            total += min(amount, Fraction(int(cap_text)))
        elif value is False:
            continue  # 明确不申索
        elif value is None or value == "unknown":
            label = rule.get("label_zh") or item_type
            questions.append(
                f"扣除项「{label}」的资格状态尚未确认：请确认是否符合对应"
                "附表的申索资格（unknown 不得默认可扣）；确认后将按法定上限计入。"
            )
        else:
            raise AppError(
                "E_INPUT_TYPE",
                f'{field}.eligibility 三态值须为 true/false/"unknown"',
                field=field,
            )
    return total


def _amount(facts: Mapping, key: str) -> Fraction:
    """事实金额 → 精确分数（C2.1 严格语法；非字符串/负数/非法 → E_INPUT_*）。"""
    raw = facts.get(key)
    if raw is None:
        raise AppError("E_INPUT_MISSING", f"缺少事实字段：{key}", field=key)
    return Fraction(Decimal(parse_amount(raw)))


def _tri_state(field: Any) -> Any:
    """读取 {value: <三态>} 形态的资格事实字段（C3.5）；缺失 → None。"""
    if isinstance(field, Mapping):
        return field.get("value")
    return None


def _canonical(value: Fraction) -> str:
    """精确分数 → canonical 金额字符串（输入小数 ≤2 位，分母必为 2/5 的幂）。"""
    if value.denominator == 1:
        return format_canonical(Decimal(value.numerator))
    return format_canonical(Decimal(value.numerator) / Decimal(value.denominator))


def _null_amounts() -> dict:
    """needs_input/blocked 六键一律显式 None（C3.3/C3.4；不填零、不缺省）。"""
    return {key: None for key in _AMOUNT_KEYS}


def _needs_input(questions: list[str]) -> dict:
    return {
        "status": "needs_input",
        "amounts": _null_amounts(),
        "questions": questions,
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }


def _blocked(reason: str) -> dict:
    return {
        "status": "blocked",
        "amounts": _null_amounts(),
        "blocked_reason": reason,
        "questions": [],
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }
