"""REQ-6/REQ-8 个人入息课税比较引擎（M3 PA 泳道；Annex D D6 为税法语义权威）。

REQ: REQ-6（SDD §9：仅比较依法可选方式；合并入息＝薪俸净额＋逐物业
（NAV 份额−利息上限）＋业务份额；利息逐物业以该人该物业 NAV 份额为上限；
标准上限按 PA 减少后总入息口径（非薪俸税复用）；重复申索阻断；各方案
宽减后比较；资格不明不输出最优）＋REQ-8（最终税/宽减/已缴暂缴/下年暂缴/
结欠退款分列；缺 PST 组件仅部分结果且 balance null）。
规格锚点:
  - s.42(1) 但书（T1）：利息＝未在 Part 4 扣除者为产生该部分总入息所借款
    项，从物业部分入息中扣除；Q33（T1，台账 1.7）＋PAM55（T2）：PA 下利息
    逐物业 min(利息, 该人该物业 NAV 份额)，非全局池、非整笔利息×份额。
  - s.42(10)（T1）：共同选择时各自先按 s.42 算（减少后）总入息 R_i，再按
    s.42A 合并；s.42A(1)＋s.29(1A)（T1）：合并总入息−Part 5 免税额（共同
    PA 一份已婚免税额覆盖两人）；累进基数＝max(0, 合并−免税额)。
  - s.43(1A)（T1）：标准上限法定基数＝PA 减少后总入息 R（附表 1 两级：
    2024/25 起首 $5,000,000 15%、余额 16%），不得复用薪俸税 NAI−Part 4A
    口径（DIPN18 ¶40 T2 佐证）。
  - D6.2a（T1-observed，三年度同构）：CompTP 原始值严格 `<`、平局取累进、
    外层单次 floor（≡两径先分别 floor 再比较、floor 后相等取累进）；
    new_STDTP 返回即 floor；宽减＝min(ceil(税×率), cap)（CalculateRebate）。
  - s.100(4)/(5)＋s.43(2B)（T1）：PA 一个宽减、先减宽减再分摊。joint 首版
    输出契约（D6.3；第三轮复审裁定）：权威金额＝宽减后 joint 总税额；个人
    份额仅以精确 Fraction 比率（apportionment_ratios＝R_i/ΣR 规范形）暴露，
    首版不输出个人整元分摊税额；ΣR=0 → 无税、不分摊、不执行除法。
  - no_pa 方案＝不选 PA 的分别评税（Q32 官方整例同构）：薪俸逐人（s.29(1)
    配偶无薪俸应评税入息时一份已婚额、否则基本额；附表 43 薪俸行各一个
    cap）＋物业逐项（floor(NAV)→floor(×15%)；附表 43 无物业行、无宽减，
    D5.0）＋非法团业务（D6.2a②：择档 floor(份额利润)≤2,000,000 → 7.5%
    全率、否则 2m×7.5%＋余额×15%、随后 floor；宽减每业务 ceil→cap）。
  - 免税额互斥（D6.3；DIPN18 ¶43/53/55 T2）：同一受养人跨人重复申索（父母；
    子女未按 s.31(3) 整笔提名）→ E_INPUT_CONTRADICTORY；受养父母缺 s.30
    年龄/供养事实 → 追问（不默认、不静默丢弃）。
  - Annex C C2/C3：金额全程 Decimal/Fraction 禁 float；status/amounts 六键
    信封；needs_input 六键一律显式 None（不填零、不部分计算）。
期望值来源: Q32（T1，台账 1.7）、Q33（T1＋T3 手算）、PAM37 例 4（T2，
  台账 1.13）＝tests/engines/test_personal_assessment.py 锚；其余组合 T3
  独立手算（算式见各测试注释，可审计复算）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from fractions import Fraction

from app.core.errors import AppError
from app.core.money import (
    ceil_at_point,
    floor_at_point,
    format_canonical,
    parse_amount,
    parse_ratio,
)

_AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)

_METHOD_PA = "personal_assessment"
_METHOD_NO_PA = "no_pa"

_BUSINESS_KINDS = frozenset({"sole_proprietorship", "partnership"})


def compare_personal_assessment(facts: Mapping, bundle: Mapping) -> dict:
    """比较个人入息课税与分开评税（no_pa）两方案宽减后终额（纯函数；SDD §3）。"""
    data = _pa_data(bundle)
    year = facts.get("year_of_assessment")
    year_data = data["allowances"].get(year) if isinstance(year, str) else None
    if year_data is None:
        return _blocked(f"课税年度 {year!r} 不在个人入息课税规则束适用范围")
    start = int(year[:4])
    ya_start, ya_end = date(start, 4, 1), date(start + 1, 3, 31)

    persons = _parse_persons(facts)
    _reject_duplicate_dependants(persons)

    questions: list[str] = []
    married = _resolve_married(facts, persons, questions)
    _resolve_rates(persons, questions)
    _parent_questions(persons, questions)
    children = _resolve_children(persons, ya_start, ya_end, questions)
    if questions:
        return _needs_input(questions)
    _prepare_property_math(persons, data)

    pa = _build_pa_scenario(
        data, year, year_data, persons, married, children, ya_start, ya_end, True
    )
    no_pa = _build_no_pa_scenario(
        data, year, year_data, persons, married, children, ya_start, ya_end, True
    )
    # 各方案宽减后终额比较（s.100(4)/(5)）；平局取 PA（确定性并列裁决）。
    best_is_pa = pa[2] <= no_pa[2]

    # 暂缴估算＝同一事实按下一年度规则、不计宽减（附表 43 不适用暂缴）；
    # 下一年度不在束内 → 退回本年度规则。
    next_year = f"{start + 1}_{(start + 2) % 100:02d}"
    next_year_data = data["allowances"].get(next_year, year_data)
    next_provisional = (
        _build_pa_scenario(
            data, year, next_year_data, persons, married, children, ya_start, ya_end, False
        )[2]
        if best_is_pa
        else _build_no_pa_scenario(
            data, year, next_year_data, persons, married, children, ya_start, ya_end, False
        )[2]
    )

    missing: list[str] = []
    paid: Fraction | None
    raw_paid = facts.get("provisional_paid")
    if raw_paid is None:  # 缺 PST 组件 → partial、balance null（REQ-8）
        paid, missing = None, ["provisional_paid"]
    else:
        paid = Fraction(Decimal(parse_amount(raw_paid)))

    best = pa if best_is_pa else no_pa
    amounts = {
        "before_reduction": best[0]["before_reduction"],
        "reduction": best[0]["reduction"],
        "final_after_reduction": best[0]["final_after_reduction"],
        "provisional_paid": None if paid is None else _canonical(paid),
        "next_provisional": _canonical(Fraction(next_provisional)),
        "balance": None if paid is None else _canonical(Fraction(best[2]) - paid),
    }
    return {
        "status": "partial" if missing else "complete",
        "amounts": amounts,
        "questions": [],
        "scenarios": [pa[0], no_pa[0]],
        "best_scenario": None if missing else (_METHOD_PA if best_is_pa else _METHOD_NO_PA),
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": missing,
    }


def _pa_data(bundle: Mapping) -> Mapping:
    """注入束的 PA 数据区守卫（缺失 → 拒算，C3.7）。"""
    data = bundle.get("data")
    pa = data.get("personal_assessment") if isinstance(data, Mapping) else None
    if isinstance(pa, Mapping) and pa:
        return pa
    raise AppError(
        "E_RULES_STATE_UNVERIFIABLE",
        "个人入息课税规则数据缺失（bundle.data.personal_assessment）；拒算。",
    )


def _parse_persons(facts: Mapping) -> list[dict]:
    """按人解析入息/物业/业务/受养人事实（结构非法 → E_INPUT_*）。"""
    raw = facts.get("persons")
    if not isinstance(raw, list) or not raw:
        raise AppError(
            "E_INPUT_MISSING", "缺少 persons[]（个人入息课税按人汇聚事实）", field="persons"
        )
    persons: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", "persons[] 元素须为对象", field="persons")
        pid = entry.get("person_id")
        if not isinstance(pid, str) or not pid:
            raise AppError("E_INPUT_MISSING", "person 缺少 person_id", field="persons")
        if pid in seen:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"person_id 重复：{pid!r}", field="persons"
            )
        seen.add(pid)
        where = f"persons[{pid}]"
        persons.append(
            {
                "person_id": pid,
                "employment_income": _required_amount(
                    entry, "employment_income", where
                ),
                "mpf": _required_amount(entry, "mpf_mandatory_contributions", where),
                "properties": _parse_properties(entry.get("properties"), where),
                "businesses": _parse_businesses(entry.get("businesses"), where),
                "parent_ids": _parse_parents(entry.get("dependent_parents"), where),
                "children_raw": _parse_children_raw(
                    entry.get("dependent_children"), where
                ),
            }
        )
    return persons


def _parse_properties(raw: object, where: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError(
            "E_INPUT_TYPE", f"{where}.properties 须为数组", field=f"{where}.properties"
        )
    properties: list[dict] = []
    for prop in raw:
        if not isinstance(prop, Mapping):
            raise AppError(
                "E_INPUT_TYPE",
                f"{where}.properties 元素须为对象",
                field=f"{where}.properties",
            )
        pid = prop.get("property_id")
        if not isinstance(pid, str) or not pid:
            raise AppError(
                "E_INPUT_MISSING", "物业缺少 property_id", field=f"{where}.properties"
            )
        pwhere = f"{where}.properties[{pid}]"
        properties.append(
            {
                "property_id": pid,
                "share": parse_ratio(_required_value(prop, "share", pwhere)),
                "rent": _required_amount(prop, "rent_received", pwhere),
                "irrecoverable": _amount(
                    prop.get("irrecoverable_rent", "0"),
                    f"{pwhere}.irrecoverable_rent",
                ),
                "deposits": _amount(
                    prop.get("deposit_offsets", "0"), f"{pwhere}.deposit_offsets"
                ),
                "interest": _amount(
                    prop.get("mortgage_interest", "0"), f"{pwhere}.mortgage_interest"
                ),
                "rates_raw": prop.get("rates_paid_by_owner"),
            }
        )
    return properties


def _parse_businesses(raw: object, where: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError(
            "E_INPUT_TYPE", f"{where}.businesses 须为数组", field=f"{where}.businesses"
        )
    businesses: list[dict] = []
    for business in raw:
        if not isinstance(business, Mapping):
            raise AppError(
                "E_INPUT_TYPE",
                f"{where}.businesses 元素须为对象",
                field=f"{where}.businesses",
            )
        bid = business.get("business_id")
        if not isinstance(bid, str) or not bid:
            raise AppError(
                "E_INPUT_MISSING", "业务缺少 business_id", field=f"{where}.businesses"
            )
        bwhere = f"{where}.businesses[{bid}]"
        kind = business.get("entity_kind")
        if not isinstance(kind, str) or kind not in _BUSINESS_KINDS:
            raise AppError(
                "E_INPUT_TYPE",
                f"{bwhere}.entity_kind 必须为 sole_proprietorship／partnership",
                field=f"{bwhere}.entity_kind",
            )
        businesses.append(
            {
                "business_id": bid,
                "kind": kind,
                "profit": _required_amount(business, "assessable_profit", bwhere),
                "share": parse_ratio(_required_value(business, "share", bwhere)),
            }
        )
    return businesses


def _parse_parents(raw: object, where: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError(
            "E_INPUT_TYPE",
            f"{where}.dependent_parents 须为数组",
            field=f"{where}.dependent_parents",
        )
    parent_ids: list[str] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise AppError(
                "E_INPUT_TYPE",
                f"{where}.dependent_parents 元素须为对象",
                field=f"{where}.dependent_parents",
            )
        parent_id = entry.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
            raise AppError(
                "E_INPUT_MISSING",
                "受养父母缺少 parent_id",
                field=f"{where}.dependent_parents",
            )
        parent_ids.append(parent_id)
    return parent_ids


def _parse_children_raw(raw: object, where: str) -> list[Mapping]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError(
            "E_INPUT_TYPE",
            f"{where}.dependent_children 须为数组",
            field=f"{where}.dependent_children",
        )
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise AppError(
                "E_INPUT_TYPE",
                f"{where}.dependent_children 元素须为对象",
                field=f"{where}.dependent_children",
            )
    return list(raw)


def _reject_duplicate_dependants(persons: list[dict]) -> None:
    """同一受养人跨人重复申索 → E_INPUT_CONTRADICTORY（D6.3 互斥）。

    父母按 parent_id 身份互斥（s.30/s.33 同一受养人单一申索）；子女按出生
    日期跨人互斥（s.31(3)：未分居夫妻须由获提名一方整组申索）。同Person
    名单内同出生日期＝双胞胎合法并存，不阻断。
    """
    parent_owner: dict[str, str] = {}
    child_owner: dict[str, str] = {}
    for person in persons:
        pid = person["person_id"]
        for parent_id in person["parent_ids"]:
            owner = parent_owner.get(parent_id)
            if owner is not None:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"受养父母 {parent_id!r} 被重复申索（{owner} 与 {pid}）；"
                    "同一受养人须指定单一申索人（s.30/s.33）。",
                    field="dependent_parents",
                )
            parent_owner[parent_id] = pid
        for child in person["children_raw"]:
            birth = child.get("birth_date")
            if not isinstance(birth, str):
                continue  # 缺失/非法出生日期由子女资格解析追问
            owner = child_owner.get(birth)
            if owner is not None:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"同一受养子女（出生日期 {birth}）被多人重复申索（{owner} 与 "
                    f"{pid}）；未分居夫妻须按 s.31(3) 由获提名一方整组申索。",
                    field="dependent_children",
                )
            child_owner[birth] = pid


def _resolve_married(facts: Mapping, persons: list[dict], questions: list[str]) -> bool:
    """婚姻状况三态：unknown/缺失 → 追问（s.29 分支无法判定，不默认）。"""
    raw = facts.get("married_status")
    value = raw.get("value") if isinstance(raw, Mapping) else None
    if value == "married":
        if len(persons) != 2:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                "已婚事实须恰有两名人士（本人与配偶）",
                field="persons",
            )
        return True
    if value == "single":
        return False
    questions.append(
        "请确认本课税年度的婚姻状况（已婚／单身），以便判定已婚人士免税额与评税方式。"
    )
    return False


def _resolve_rates(persons: list[dict], questions: list[str]) -> None:
    """s.5(1A)(b)(i)：差饷扣除须业主同意缴付且已缴付（两条件）。

    正数申报而任一条件 unknown → 追问（不得默认可扣/不可扣）。
    """
    for person in persons:
        for prop in person["properties"]:
            raw = prop.pop("rates_raw")
            if raw is None:
                prop["rates"] = Fraction(0)  # 缺省＝无差饷事实、无可扣额
                continue
            if not isinstance(raw, Mapping):
                raise AppError(
                    "E_INPUT_TYPE",
                    "rates_paid_by_owner 须为对象 {amount, agreed{value}, actually_paid{value}}",
                    field="rates_paid_by_owner",
                )
            where = (
                f"persons[{person['person_id']}].properties[{prop['property_id']}]"
                ".rates_paid_by_owner"
            )
            amount_text = parse_amount(raw.get("amount", "0"))
            amount = Fraction(Decimal(amount_text))
            agreed = _tri_state(raw.get("agreed"), f"{where}.agreed")
            actually_paid = _tri_state(raw.get("actually_paid"), f"{where}.actually_paid")
            if amount > 0 and "unknown" in (agreed, actually_paid):
                questions.append(
                    f"物业 {prop['property_id']}：申报差饷 {amount_text}（正数），"
                    "请确认业主是否同意缴付（s.5(1A)(b)(i)）以及差饷是否已实际缴付"
                    "——两条件未确认前不得扣除。"
                )
                prop["rates"] = None
                continue
            prop["rates"] = amount if agreed is True and actually_paid is True else Fraction(0)


def _parent_questions(persons: list[dict], questions: list[str]) -> None:
    """受养父母申索：schema 仅含 residence，缺 s.30 年龄/供养事实 → 追问。"""
    for person in persons:
        for parent_id in person["parent_ids"]:
            questions.append(
                f"请提供受养父母（{parent_id}）的年龄（是否年满 60 岁或 55–59 岁）、"
                "是否通常居于香港及全年供养支出事实（s.30），以便判定父母免税额资格。"
            )


def _resolve_children(
    persons: list[dict], ya_start: date, ya_end: date, questions: list[str]
) -> dict[str, list[date]]:
    """子女免税额资格（s.31）：未成年＋通常居于香港；18 岁或以上/居港未知
    → 追问；确认非居港或年度结束后出生 → 不构成供养事实。"""
    result: dict[str, list[date]] = {}
    for person in persons:
        children: list[date] = []
        for child in person["children_raw"]:
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
            if residence_value is False or birth > ya_end:
                continue
            if not _under_age(birth, ya_end, 18):
                questions.append(
                    f"请确认出生日期为 {birth_raw} 的受养子女在课税年度末的"
                    "教育／资格状况（18 岁或以上须全日制就学或因伤残不能工作）。"
                )
                continue
            children.append(birth)
        result[person["person_id"]] = children
    return result


def _under_age(birth: date, on: date, years: int) -> bool:
    """on 当日是否未满 years 岁（2/29 出生按平年 2/28 计满岁日）。"""
    try:
        threshold = birth.replace(year=birth.year + years)
    except ValueError:  # 2/29 → 平年无此日
        threshold = birth.replace(year=birth.year + years, day=28)
    return on < threshold


def _prepare_property_math(persons: list[dict], data: Mapping) -> None:
    """逐物业 NAV（s.5(1A)：(AV−差饷)×(1−1/5) 修葺免税额，floor）→ 份额
    V_ip＝NAV×share → 利息上限 I_ip＝min(利息, V_ip)（s.42(1) 但书＋Q33/
    PAM55：逐物业封顶，可降至 NAV 份额为零）。"""
    repair = Fraction(1) - parse_ratio(data["repair_allowance_ratio"])
    for person in persons:
        for prop in person["properties"]:
            net_irrecoverable = max(
                Fraction(0), prop["irrecoverable"] - prop["deposits"]
            )
            av = prop["rent"] - net_irrecoverable - prop["rates"]
            nav = floor_at_point(av * repair)
            nav_share = nav * prop["share"]
            prop["nav"] = nav
            prop["nav_share"] = nav_share
            prop["interest_deducted"] = min(prop["interest"], nav_share)


def _build_pa_scenario(
    data: Mapping,
    year: str,
    year_data: Mapping,
    persons: list[dict],
    married: bool,
    children: Mapping[str, list[date]],
    ya_start: date,
    ya_end: date,
    apply_rebate: bool,
) -> tuple[dict, int, int]:
    """s.42/s.42A 评税：逐人 R_i →（已婚）合并 → Part 5 免税额 → 累进与
    s.43(1A) 标准上限两径择低 → 附表 43 一个宽减（共同一份、分别各一份）。

    返回 (scenario 信封, before, final)；信封金额为 canonical 字符串。
    """
    mpf_cap = Fraction(int(data["mpf_deduction_cap"]))
    person_rows: list[dict] = []
    reduced_incomes: list[Fraction] = []
    for person in persons:
        total = person["employment_income"]
        for prop in person["properties"]:
            total += prop["nav_share"] - prop["interest_deducted"]
        for business in person["businesses"]:
            total += business["profit"] * business["share"]
        reduced = max(Fraction(0), total - min(person["mpf"], mpf_cap))
        reduced_incomes.append(reduced)
        person_rows.append(
            {
                "person_id": person["person_id"],
                "reduced_total_income": _canonical(reduced),
                "properties": [
                    {
                        "property_id": prop["property_id"],
                        "nav_share": _canonical(prop["nav_share"]),
                        "interest_deducted": _canonical(prop["interest_deducted"]),
                    }
                    for prop in person["properties"]
                ],
            }
        )
    total_reduced = sum(reduced_incomes, Fraction(0))
    if married:
        pooled = [
            birth for person in persons for birth in children[person["person_id"]]
        ]
        assessments = [
            (
                total_reduced,
                int(year_data["married"])
                + _child_allowance_total(pooled, year_data, ya_start, ya_end),
            )
        ]
    else:
        assessments = [
            (
                reduced,
                int(year_data["basic"])
                + _child_allowance_total(
                    children[person["person_id"]], year_data, ya_start, ya_end
                ),
            )
            for person, reduced in zip(persons, reduced_incomes, strict=True)
        ]

    before = 0
    reduction = 0
    for base, allowance in assessments:
        nci = max(Fraction(0), base - allowance)
        progressive = floor_at_point(_band_tax(nci, data["progressive_bands"]))
        # s.43(1A)：标准上限基数＝PA 减少后总入息（非薪俸税 NAI−Part 4A 口径）。
        standard = floor_at_point(_band_tax(base, data["standard_rate_bands"]))
        # D6.2a：平局取累进（外层单次 floor 等价于先分别 floor 再比较）。
        tax = progressive if progressive <= standard else standard
        before += tax
        if apply_rebate:
            reduction += _rebate(tax, data["reductions"].get(year))
    final = before - reduction

    scenario: dict = {
        "method": _METHOD_PA,
        "before_reduction": str(before),
        "reduction": str(reduction),
        "final_after_reduction": str(final),
        "persons": person_rows,
    }
    if married:
        scenario["apportionment_ratios"] = _apportionment_ratios(
            persons, reduced_incomes, total_reduced
        )
    return scenario, before, final


def _apportionment_ratios(
    persons: list[dict], reduced_incomes: list[Fraction], total_reduced: Fraction
) -> dict[str, str]:
    """s.43(2B)＋D6.3 首版契约：仅输出 R_i/ΣR 精确分数规范形（无个人整元
    税额）；ΣR=0 → 无税、不分摊、不执行除法。"""
    if total_reduced == 0:
        return {}
    ratios: dict[str, str] = {}
    for person, reduced in zip(persons, reduced_incomes, strict=True):
        share = reduced / total_reduced
        ratios[person["person_id"]] = f"{share.numerator}/{share.denominator}"
    return ratios


def _build_no_pa_scenario(
    data: Mapping,
    year: str,
    year_data: Mapping,
    persons: list[dict],
    married: bool,
    children: Mapping[str, list[date]],
    ya_start: date,
    ya_end: date,
    apply_rebate: bool,
) -> tuple[dict, int, int]:
    """分开评税（no_pa）：薪俸逐人（附表 43 薪俸行各一个 cap）＋物业逐项
    （无宽减，D5.0）＋非法团业务（D6.2a 两级分支、每业务一个宽减）。"""
    mpf_cap = Fraction(int(data["mpf_deduction_cap"]))
    sal_before = 0
    sal_reduction = 0
    for index, person in enumerate(persons):
        nai = max(
            Fraction(0), person["employment_income"] - min(person["mpf"], mpf_cap)
        )
        if married:
            # s.29(1)(a)：配偶无薪俸应评税入息 → 本人身士一份已婚免税额；
            # 双方均有 → s.10(1) 默认分别评税、各按基本免税额。
            other = persons[len(persons) - 1 - index]
            base_allowance = (
                int(year_data["married"]) if other["employment_income"] == 0 else int(year_data["basic"])
            )
        else:
            base_allowance = int(year_data["basic"])
        allowance = base_allowance + _child_allowance_total(
            children[person["person_id"]], year_data, ya_start, ya_end
        )
        nci = max(Fraction(0), nai - allowance)
        progressive = floor_at_point(_band_tax(nci, data["progressive_bands"]))
        standard = floor_at_point(_band_tax(nai, data["standard_rate_bands"]))
        tax = progressive if progressive <= standard else standard
        sal_before += tax
        if apply_rebate:
            sal_reduction += _rebate(tax, data["reductions"].get(year))

    prop_rate = parse_ratio(data["property_standard_rate"])
    prop_before = sum(
        floor_at_point(prop["nav"] * prop_rate)
        for person in persons
        for prop in person["properties"]
    )

    prof_before = 0
    prof_reduction = 0
    has_business = any(person["businesses"] for person in persons)
    if has_business:
        tier = data["profits_two_tier"]
        threshold = Fraction(int(tier["threshold"]))
        low = Fraction(int(tier["low_rate_bp"]), 10000)
        high = Fraction(int(tier["high_rate_bp"]), 10000)
        for person in persons:
            for business in person["businesses"]:
                share_profit = business["profit"] * business["share"]
                # D6.2a②：择档取整点＝floor(份额利润)≤门槛 → 低档全率；
                # 税额取整点＝条件分支 raw 计算后 floor。
                if floor_at_point(share_profit) <= threshold:
                    tax = floor_at_point(share_profit * low)
                else:
                    tax = floor_at_point(
                        threshold * low + (share_profit - threshold) * high
                    )
                prof_before += tax
                if apply_rebate:
                    prof_reduction += _rebate(tax, data["profits_reductions"].get(year))

    components = [
        {
            "tax_type": "salaries",
            "before_reduction": str(sal_before),
            "reduction": str(sal_reduction),
            "final_after_reduction": str(sal_before - sal_reduction),
        }
    ]
    if any(person["properties"] for person in persons):
        components.append(
            {
                "tax_type": "property",
                "before_reduction": str(prop_before),
                "reduction": "0",  # 附表 43 无物业税行（D5.0）
                "final_after_reduction": str(prop_before),
            }
        )
    if has_business:
        components.append(
            {
                "tax_type": "profits",
                "before_reduction": str(prof_before),
                "reduction": str(prof_reduction),
                "final_after_reduction": str(prof_before - prof_reduction),
            }
        )
    before = sal_before + prop_before + prof_before
    reduction = sal_reduction + prof_reduction
    scenario = {
        "method": _METHOD_NO_PA,
        "before_reduction": str(before),
        "reduction": str(reduction),
        "final_after_reduction": str(before - reduction),
        "components": components,
    }
    return scenario, before, before - reduction


def _child_allowance_total(
    children: list[date], year_data: Mapping, ya_start: date, ya_end: date
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


def _band_tax(base: Fraction, bands: list) -> Fraction:
    """附表 2/附表 1 带状计税（Fraction，全程无 float）；末段无 width＝余额段。"""
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


def _rebate(before: int, entry: object) -> int:
    """附表 43：min(ceil(税×率), cap, 税额)（D6.2a CalculateRebate＝ceil）；
    缺项年度（2026/27）＝无宽减；暂缴不适用（apply_rebate=False 不进入）。"""
    if not isinstance(entry, Mapping):
        return 0
    rate = Fraction(int(entry["percent_bp"]), 10000)
    cap = floor_at_point(Fraction(Decimal(parse_amount(entry["cap"]))))
    return min(ceil_at_point(Fraction(before) * rate), cap, before)


def _tri_state(raw: object, field: str) -> bool | str:
    """C3.5 三态事实：true/false/"unknown"；缺省（None）视为 unknown。"""
    value = raw.get("value") if isinstance(raw, Mapping) else raw
    if value is True or value is False:
        return value
    if value is None or value == "unknown":
        return "unknown"
    raise AppError(
        "E_INPUT_TYPE", f'{field} 三态值须为 true/false/"unknown"', field=field
    )


def _amount(value: object, field: str) -> Fraction:
    """C2.1 金额 → 精确有理（金额链路零浮点）。"""
    try:
        text = parse_amount(value)
    except AppError as exc:
        raise AppError(exc.code, f"{field}：{exc.message_zh}", field=field) from exc
    return Fraction(Decimal(text))


def _required_value(container: Mapping, key: str, where: str) -> object:
    if key not in container:
        raise AppError("E_INPUT_MISSING", f"{where} 缺少 {key}", field=f"{where}.{key}")
    return container[key]


def _required_amount(container: Mapping, key: str, where: str) -> Fraction:
    value = _required_value(container, key, where)
    return _amount(value, f"{where}.{key}")


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
        "scenarios": [],
        "best_scenario": None,
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
        "scenarios": [],
        "best_scenario": None,
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }
