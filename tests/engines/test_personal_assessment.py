"""REQ-6/REQ-8 个人入息课税比较引擎（M3 Red；Annex D D6 为税法语义权威）。

REQ: REQ-6（SDD §9：仅比较依法可选方式；合并入息＋NAV＋自业份额；利息以各
物业 NAV 份额为上限；标准上限按 PA 减少后总入息口径（非薪俸税复用）；重复
申索阻断；宽减后比较各方案；资格不明不输出最优）＋REQ-8（最终税/宽减/已缴
暂缴/下年暂缴/结欠退款分列；缺 PST 组件仅部分结果且 balance null）。
规格锚点:
  - SDD §6 个人入息课税行＋§9 REQ-6/REQ-8；Annex D D6.0（法定基础
    s.41/s.42/s.42A/s.43/s.100/附表 1/2/43，T1-现行法例文本，台账 1.17）、
    D6.1（资格与选择）、D6.2（计算结构）、D6.2a（官方 PA 计算器三年度
    observed 行为，台账 1.11）、D6.3（宽减、联名与分配）。
  - s.42(1) 但书（T1）：共同持有均分 NAV、分权共有按份额；利息＝未在
    Part 4 扣除者为产生该部分总入息所借款项，从物业部分入息中扣除；
    Q33（T1，台账 1.7）：PA 下利息扣除以 **NAV 份额** 为限——逐物业
    min(利息, 该人该物业 NAV 份额)，非全局池、非整笔利息×份额（PAM55
    T2 佐证：可降至 NAV 为零）。
  - s.42(10)（T1）：共同选择时各自先按 s.42 计算（减少后）总入息 R_i，
    然后才按 s.42A 合并。
  - s.43(1A)（T1）：PA 税额不得超过以标准税率对「s.42(2) 及 (5) 减少后
    总入息（或合并总入息）」全额征收之数——标准上限法定基数＝PA 减少后
    总入息 R，**不得**复用薪俸税 NAI−Part 4A 口径（DIPN18 ¶40 T2 佐证）。
  - 附表 1（T1）：2024/25 起 PA 两级标准（首 $5,000,000 15%、余额 16%）；
    附表 2（T1）：2018/19 起累进 2/6/10/14/17%（五段 $50k）；
    附表 43（T1）：2024/25 100%/$1,500、2025/26 100%/$3,000、2026/27
    无条目（每年度薪俸/利得/PA 三行、无物业税行）。
  - s.100(4)/(5)＋s.43(2B)（T1）：PA 宽减后、joint 分摊前；**先减宽减**，
    再按各自减少后总入息占合并总入息比例分摊。
  - **joint PA 首版输出契约（D6.3；第三轮复审裁定）**：权威金额＝**宽减后
    joint 总税额**；个人份额仅内部以**精确 Fraction** 保留；首版**不输出
    个人整元分摊税额**；`ΣR＝0` → 无税、不分摊、不执行除法。
  - D6.2a（T1-observed，三年度同构）：CompTP 原始值严格 `<` 比较、平局取
    累进、外层单次 floor；PACOut[41]=floor(累进)、[42]=new_STDTP、[43]=min。
  - 免税额互斥（D6.3；DIPN18 ¶43/53/55 T2 佐证）：basic/married 跨人互斥、
    同一受养人不重复申索、子女免税额 s.31(3) 未分居夫妻由获提名一方整组
    申索——重复申索 → E_INPUT_CONTRADICTORY（C3.7；REQ-10 矛盾资料拒算）。
  - 缺 PST 组件 → partial＋balance null、仅已知最终部分（SDD §3/REQ-8）。
期望值来源:
  - Q32（2025/26 官方完整示例，T1，台账 1.7）：薪金 250,000＋租金 240,000
    →NAV 192,000；PA：442,000−264,000=178,000 → 累进 12,920−宽减 3,000
    =9,920；分开评税：物业税 28,800＋薪俸税 0；**夫妻/合并事实整体保留**。
  - Q33（T1）：利息扣除以 NAV 份额为限（本测试数值为 T3 独立手算）。
  - PAM37 例 4（T2，台账 1.13）：独资 480,000＋租金 NAV 120,000 → R
    600,000；已婚 264,000 → NCI 336,000；39,120−3,000=36,120；分摊比例
    480/600→4/5、120/600→1/5（内部精确分数 28,896/7,224 不作产品输出）。
  - s.43(1A)/附表 1/43 标准上限与宽减数值＝T1（台账 1.17）；其余组合数值
    为 T3 独立手算（算式见各测试注释，可审计复算）。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.engines.personal_assessment.compare_personal_assessment(
    facts: Mapping, bundle: Mapping) -> dict：纯函数、注入不可变 RuleBundle；
    facts 字段名沿用 Annex C C3.5 薪俸税族命名（year_of_assessment／
    employment_income／mpf_mandatory_contributions／married_status{value}／
    dependent_children[]{birth_date,residence}）＋【拟名】扩展：
      - persons[]{person_id, employment_income, mpf_mandatory_contributions,
        properties[]{property_id, share:RatioStr, rent_received,
        rates_paid_by_owner{amount,agreed{value},actually_paid{value}},
        irrecoverable_rent, deposit_offsets, mortgage_interest},
        businesses[]{business_id, entity_kind ∈ {sole_proprietorship,
        partnership}, assessable_profit, share:RatioStr},
        dependent_parents[]{parent_id, residence{value}}}；
      - 顶层 provisional_paid（已缴暂缴，三态；缺失 → partial、balance null）。
  - 返回信封：status ∈ {complete, partial, needs_input, blocked}（C3.3）；
    amounts 恰含 C3.4 六键（推荐方案口径；partial 时仅已知最终部分、
    balance=None）；questions[]（needs_input 时非空中文清单）。
  - scenarios[]：每方案一項 {method, before_reduction/total_before_reduction,
    reduction/total_reduction, final_after_reduction, components[]（no_pa
    方案按税种分列）}；method ∈ {"personal_assessment", "no_pa"}。
  - personal_assessment 方案另带 persons[]{person_id, reduced_total_income,
    properties[]{property_id, nav_share, interest_deducted}}（逐物业利息
    上限可审计）；joint（已婚共同）另带 apportionment_ratios{person_id:
    RatioStr}（R_i/ΣR 精确分数规范形）——**仅比率，无个人整元税额**。
  - best_scenario：各方案**宽减后**终额比较的最低者（method 字符串）；
    partial/needs_input/blocked 时为 None（不得宣称最优）。
  - 资格 unknown（如 married_status）→ status="needs_input"，amounts 六键
    一律显式 None，不部分计算；同一受养人跨人重复申索（父母/子女未按
    s.31(3) 整笔提名）→ 抛 app.core.errors.AppError(
    code="E_INPUT_CONTRADICTORY")。
  - 本文件对 app.engines.* 延迟导入（测试体内），使 Red 阶段每个测试的
    失败原因对应「缺失的 app.engines.personal_assessment 模块」，而非
    收集期整体失败。
"""

from __future__ import annotations

import json
import re

from app.core.errors import AppError

_METHOD_PA = "personal_assessment"
_METHOD_NO_PA = "no_pa"

_AMOUNT_KEYS = {
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
}


def _calc():
    """延迟导入被测引擎与规则束（Red 失败原因＝缺失 app.engines.personal_assessment）。"""
    from app.engines.personal_assessment import compare_personal_assessment
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return compare_personal_assessment, INITIAL_BUNDLE_CONTENT


def _property(pid: str, rent: str, *, share: str = "1/1", interest: str = "0") -> dict:
    """单一物业事实（差饷两条件显式为零；份额默认全权拥有）。"""
    return {
        "property_id": pid,
        "share": share,
        "rent_received": rent,
        "rates_paid_by_owner": {
            "amount": "0",
            "agreed": {"value": True},
            "actually_paid": {"value": True},
        },
        "irrecoverable_rent": "0",
        "deposit_offsets": "0",
        "mortgage_interest": interest,
    }


def _person(
    pid: str,
    *,
    salary: str = "0",
    mpf: str = "0",
    properties: list | None = None,
    businesses: list | None = None,
    parents: list | None = None,
    children: list | None = None,
) -> dict:
    return {
        "person_id": pid,
        "employment_income": salary,
        "mpf_mandatory_contributions": mpf,
        "properties": list(properties or []),
        "businesses": list(businesses or []),
        "dependent_parents": list(parents or []),
        "dependent_children": list(children or []),
    }


def _facts(year: str, *persons: dict, married: str = "married", **extra) -> dict:
    facts = {
        "year_of_assessment": year,
        "married_status": {"value": married},
        "persons": list(persons),
        "provisional_paid": "0",
    }
    facts.update(extra)
    return facts


def _scenario(result: dict, method: str) -> dict:
    """按 method 取比较方案；缺失即失败（方案枚举是契约的一部分）。"""
    for scenario in result.get("scenarios", []):
        if scenario.get("method") == method:
            return scenario
    raise AssertionError(
        f"比较结果缺少方案 {method!r}，实际：{result.get('scenarios')!r}"
    )


def _assert_amounts_all_null(result: dict) -> None:
    """needs_input/blocked：六个金额键一律显式 None（C3.3；不填零、不缺省）。"""
    assert set(result["amounts"].keys()) == _AMOUNT_KEYS, (
        f"amounts 必须恰含 C3.4 六键，实际：{sorted(result['amounts'])!r}"
    )
    assert all(v is None for v in result["amounts"].values()), (
        f"不得输出任何金额（资格不明不填零），实际：{result['amounts']!r}"
    )


def _assert_chinese_questions(result: dict) -> None:
    questions = result.get("questions")
    assert isinstance(questions, list) and questions, (
        "needs_input 必须携带非空 questions[]（REQ-10 中文问题清单）"
    )
    assert any(
        re.search(r"[\u4e00-\u9fff]", q if isinstance(q, str) else json.dumps(q, ensure_ascii=False))
        for q in questions
    ), "问题清单必须为中文"


def test_pa_q32_example_9920_vs_28800() -> None:
    """Q32（2025/26 官方完整示例，T1）：PA 9,920 vs 分开评税 28,800＋0。

    夫妻事实整体保留（不得拆成独立单项 fixture）：本人薪金 250,000、配偶
    持有物业租金 240,000（差饷 0）→ NAV=240,000×80%=192,000。
    手算（Q32＋附表 2/43，T1）：
      PA（共同选择，s.42A 合并；s.29(1A) 一份已婚免税额覆盖两人）：
        合并总入息 R = 250,000＋192,000 = 442,000
        NCI = 442,000 − 264,000 = 178,000
        累进 = 50k×2%＋50k×6%＋50k×10%＋28k×14% = 12,920
        标准 = 442,000×15% = 66,300 → 取累进 12,920
        宽减 = min(12,920×100%, 3,000) = 3,000 → final 9,920
      分开评税（no_pa）：配偶无受雇入息 → 本人薪俸税按已婚免税额
        NCI = max(0, 250,000−264,000) = 0 → 税 0；
        配偶物业税 = floor(192,000×15%) = 28,800（无宽减，附表 43 无物业税行）
        → 合计 28,800；PA 9,920 < 28,800 → 推荐共同 PA。
    """
    compare, bundle = _calc()
    facts = _facts(
        "2025_26",
        _person("self", salary="250000"),
        _person("spouse", properties=[_property("p-1", "240000")]),
    )
    result = compare(facts, bundle)

    assert result["status"] == "complete", result
    pa = _scenario(result, _METHOD_PA)
    assert pa["before_reduction"] == "12920", pa
    assert pa["reduction"] == "3000", pa
    assert pa["final_after_reduction"] == "9920", pa

    no_pa = _scenario(result, _METHOD_NO_PA)
    assert no_pa["final_after_reduction"] == "28800", no_pa
    components = {
        c["tax_type"]: c["final_after_reduction"] for c in no_pa["components"]
    }
    assert components == {"salaries": "0", "property": "28800"}, no_pa

    assert result["best_scenario"] == _METHOD_PA, result
    assert result["amounts"]["final_after_reduction"] == "9920", result["amounts"]


def test_pa_interest_cap_per_property_nav_share() -> None:
    """Q33（T1）：利息逐物业 min(利息, 该人该物业 NAV 份额)；非全局池。

    手算（T3；s.42(1) 但书＋Q33）：
      物业 A：租金 200,000 → NAV 160,000；利息 200,000 > NAV 份额 → 扣 160,000
      物业 B：租金 100,000 → NAV 80,000；利息 10,000 ≤ NAV 份额 → 扣 10,000
      R = 0(薪金) ＋ (160,000−160,000) ＋ (80,000−10,000) = 70,000
      （错误全局池：min(210,000, 240,000)=210,000 → R=30,000，可区分；
        A 的超额利息不得溢出扣减 B 的 NAV 份额。）
    """
    compare, bundle = _calc()
    facts = _facts(
        "2025_26",
        _person(
            "self",
            properties=[
                _property("p-a", "200000", interest="200000"),
                _property("p-b", "100000", interest="10000"),
            ],
        ),
        married="single",
    )
    result = compare(facts, bundle)

    pa = _scenario(result, _METHOD_PA)
    person = next(p for p in pa["persons"] if p["person_id"] == "self")
    by_id = {d["property_id"]: d for d in person["properties"]}
    # 逐物业利息上限（Q33）：A 扣 min(200,000, 160,000)=160,000；B 扣 10,000
    assert by_id["p-a"]["nav_share"] == "160000", by_id
    assert by_id["p-a"]["interest_deducted"] == "160000", by_id
    assert by_id["p-b"]["nav_share"] == "80000", by_id
    assert by_id["p-b"]["interest_deducted"] == "10000", by_id
    # R（减少后总入息）＝240,000−170,000＝70,000（全局池错误路径＝30,000）
    assert person["reduced_total_income"] == "70000", person
    # 免税额 132,000 > R → PA 税 0（标准径 70,000×15%=10,500 → 取 0）
    assert pa["final_after_reduction"] == "0", pa


def test_pa_standard_cap_pa_reduced_income_not_salary_reuse() -> None:
    """s.43(1A)：标准上限基数＝PA 减少后总入息 R（含物业/业务成分），非薪俸净收入。

    手算（T3；附表 1 两级标准＋附表 43，T1）：
      R = 200,000(薪金) ＋ 11,760,000×80%(=9,408,000) = 9,608,000
      累进 = (9,608,000−132,000=9,476,000) → 16,000＋9,276,000×17% = 1,592,920
      标准（对 R 两级）= 5,000,000×15% ＋ 4,608,000×16% = 1,487,280 → 上限绑定
      宽减 3,000 → final = 1,484,280
      （错误复用薪俸口径：200,000×15%=30,000 当上限 → final 27,000，可区分。）
    """
    compare, bundle = _calc()
    facts = _facts(
        "2025_26",
        _person("self", salary="200000", properties=[_property("p-1", "11760000")]),
        married="single",
    )
    result = compare(facts, bundle)

    pa = _scenario(result, _METHOD_PA)
    assert pa["before_reduction"] == "1487280", pa
    assert pa["reduction"] == "3000", pa
    assert pa["final_after_reduction"] == "1484280", pa


def test_pa_compare_after_rebate_not_before() -> None:
    """s.100(4)：各方案在每项宽减之后比较；partial 候选不宣称最优。

    手算（T3；附表 43：PA/薪俸各一个 cap、物业税无宽减）：
      PA：R = 150,000＋1,000,000×80%(=800,000) = 950,000
        累进 = (950,000−132,000=818,000) → 16,000＋618,000×17% = 121,060
        标准 = 950,000×15% = 142,500 → 121,060；宽减 3,000 → **118,060**
      分开评税（宽减前合计 120,360 **<** PA 宽减前 121,060）：
        物业税 = 800,000×15% = 120,000（无宽减）
        薪俸税 = NCI 18,000 → 360；宽减 360 → 0
        → 宽减后合计 **120,000**
      宽减后：PA 118,060 < 120,000 → 最优＝PA（若按宽减前比较会错选分开评税）。
    """
    compare, bundle = _calc()
    facts = _facts(
        "2025_26",
        _person("self", salary="150000", properties=[_property("p-1", "1000000")]),
        married="single",
    )
    result = compare(facts, bundle)

    assert result["best_scenario"] == _METHOD_PA, (
        "比较必须在各方案宽减之后进行（118,060 < 120,000 → PA）",
        result.get("scenarios"),
    )
    assert _scenario(result, _METHOD_PA)["final_after_reduction"] == "118060"
    assert _scenario(result, _METHOD_NO_PA)["final_after_reduction"] == "120000"
    assert result["amounts"]["final_after_reduction"] == "118060", result["amounts"]

    # —— partial 候选不宣称最优：缺 PST 组件（provisional_paid）→ 无 best ——
    partial_facts = {k: v for k, v in facts.items() if k != "provisional_paid"}
    partial = compare(partial_facts, bundle)
    assert partial["status"] == "partial", partial
    assert partial.get("best_scenario") is None, (
        "partial 候选不得宣称最优（REQ-8：缺组件仅给已知最终部分）",
        partial.get("best_scenario"),
    )


def test_pa_duplicate_claim_blocked() -> None:
    """同一受养人跨人重复申索（D6.3 免税额互斥；s.31(3) 整笔提名）→ 拒算。"""
    compare, bundle = _calc()

    # —— 同一受养父母被夫妻两人各自申索（parent 与身份不重复，跨人互斥）——
    both_parent = _facts(
        "2025_26",
        _person("self", parents=[{"parent_id": "father-1", "residence": {"value": True}}]),
        _person(
            "spouse",
            parents=[{"parent_id": "father-1", "residence": {"value": True}}],
        ),
    )
    # —— 同一子女被夫妻两人分别整笔申索（未按 s.31(3) 由获提名一方申索）——
    both_child = _facts(
        "2025_26",
        _person(
            "self",
            children=[{"birth_date": "2018-05-01", "residence": {"value": True}}],
        ),
        _person(
            "spouse",
            children=[{"birth_date": "2018-05-01", "residence": {"value": True}}],
        ),
    )
    for label, facts in (
        ("受养父母重复申索", both_parent),
        ("子女未按 s.31(3) 整笔提名而双方申索", both_child),
    ):
        try:
            compare(facts, bundle)
        except AppError as exc:
            assert exc.code == "E_INPUT_CONTRADICTORY", (
                f"{label}应以 E_INPUT_CONTRADICTORY 拒算，实际 {exc.code!r}"
            )
        else:
            raise AssertionError(
                f"{label}属矛盾资料（D6.3 免税额互斥），必须拒算而非静默采纳"
            )


def test_pa_unknown_qualification_no_optimal_claim() -> None:
    """资格 unknown（婚姻状况）→ needs_input＋中文追问；不输出任何「最优」断言。"""
    compare, bundle = _calc()
    facts = _facts(
        "2025_26",
        _person("self", salary="250000"),
        _person("spouse", properties=[_property("p-1", "240000")]),
        married="unknown",
    )
    result = compare(facts, bundle)

    assert result["status"] == "needs_input", result
    _assert_chinese_questions(result)
    _assert_amounts_all_null(result)
    assert result.get("best_scenario") is None, (
        "资格不明不得输出最优方案断言（REQ-6）",
        result.get("best_scenario"),
    )


def test_pa_joint_output_total_authoritative_no_per_person_whole() -> None:
    """joint 首版输出：权威＝宽减后 joint 总额；仅精确 Fraction 比率、无个人整元税额。

    手算（T3；附表 2/43，T1）：
      R_self=300,000、R_spouse=200,000 → 合并 500,000 − 264,000 = 236,000
      累进 = 16,000＋36,000×17% = 22,120；标准 = 500,000×15% = 75,000 → 累进
      宽减 3,000 → joint 总额 19,120（权威金额）
      比率 R_i/ΣR：300,000/500,000=3/5、200,000/500,000=2/5（gcd 规范形）
    ΣR=0（双方零入息）：无税、不分摊、不执行除法（D6.3）。
    """
    compare, bundle = _calc()

    result = compare(
        _facts("2025_26", _person("self", salary="300000"), _person("spouse", salary="200000")),
        bundle,
    )
    pa = _scenario(result, _METHOD_PA)
    assert pa["final_after_reduction"] == "19120", pa
    # 个人份额仅以精确分数（RatioStr）暴露；不得输出个人整元分摊税额
    assert pa["apportionment_ratios"] == {"self": "3/5", "spouse": "2/5"}, pa
    for ratio in pa["apportionment_ratios"].values():
        assert isinstance(ratio, str) and "/" in ratio, (
            f"份额必须为精确分数 RatioStr，非整元金额：{ratio!r}"
        )
    for forbidden in ("per_person_tax", "per_person_taxes", "apportioned_tax", "individual_allocation"):
        assert forbidden not in pa, f"首版不得输出个人整元分摊税额（发现 {forbidden!r}）"

    # —— ΣR=0：双方零入息 → 无税、不分摊（不执行除法，不产生 NaN/异常）——
    zero = compare(_facts("2025_26", _person("self"), _person("spouse")), bundle)
    pa_zero = _scenario(zero, _METHOD_PA)
    assert pa_zero["final_after_reduction"] == "0", pa_zero
    assert not pa_zero.get("apportionment_ratios"), (
        "ΣR=0 无税不分摊（D6.3）",
        pa_zero.get("apportionment_ratios"),
    )


def test_missing_pst_partial_no_total_balance() -> None:
    """REQ-8：缺 PST 组件（provisional_paid）→ partial、balance null、无总额结欠。"""
    compare, bundle = _calc()
    facts = _facts(
        "2025_26",
        _person("self", salary="250000"),
        _person("spouse", properties=[_property("p-1", "240000")]),
    )
    del facts["provisional_paid"]
    result = compare(facts, bundle)

    assert result["status"] == "partial", result
    # 已知最终部分仍可输出（Q32 链 → PA final 9,920）
    assert result["amounts"]["final_after_reduction"] == "9920", result["amounts"]
    # 缺组件 → 不得输出结欠/退款总额（SDD §3/REQ-8）
    assert result["amounts"]["balance"] is None, result["amounts"]
    assert result["amounts"]["provisional_paid"] is None, result["amounts"]
    assert "provisional_paid" in result.get("missing_components", []), result


def test_pa_pam37_example4_joint_rebate_then_ratio_apportion_internal_fractions() -> None:
    """PAM37 例 4（T2）：39,120−3,000=36,120；内部精确比例 4/5 与 1/5。

    手算（PAM37 例 4＋附表 2/43，T1）：
      本人独资利润 480,000；配偶物业租金 150,000 → NAV 120,000
      R_self=480,000、R_spouse=120,000 → 合并 600,000（s.42(10) 各自先算）
      NCI = 600,000 − 264,000 = 336,000
      累进 = 16,000＋136,000×17% = 39,120；标准 = 600,000×15% = 90,000 → 累进
      宽减 3,000 → joint 总额 36,120（权威金额）
      内部精确分数份额：36,120×4/5=28,896、36,120×1/5=7,224——**仅验内部
      比例与 joint 总额**（D6.3 首版输出契约），个人整元不作产品输出。
    """
    compare, bundle = _calc()
    facts = _facts(
        "2025_26",
        _person(
            "self",
            businesses=[
                {
                    "business_id": "b-1",
                    "entity_kind": "sole_proprietorship",
                    "assessable_profit": "480000",
                    "share": "1/1",
                }
            ],
        ),
        _person("spouse", properties=[_property("p-1", "150000")]),
    )
    result = compare(facts, bundle)

    pa = _scenario(result, _METHOD_PA)
    assert pa["before_reduction"] == "39120", pa
    assert pa["reduction"] == "3000", pa
    assert pa["final_after_reduction"] == "36120", pa
    # s.43(2B)：按各自减少后总入息比例（宽减后分摊；内部精确 Fraction）
    assert pa["apportionment_ratios"] == {"self": "4/5", "spouse": "1/5"}, pa
    # 28,896/7,224 为 PAM37 官方例内部精确分数证据——不得作为产品输出断言
    for forbidden in ("per_person_tax", "per_person_taxes", "apportioned_tax"):
        assert forbidden not in pa, forbidden
    assert result["amounts"]["final_after_reduction"] == "36120", result["amounts"]
