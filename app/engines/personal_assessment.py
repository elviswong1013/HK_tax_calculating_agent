# -*- coding: utf-8 -*-
"""REQ-2/6/8/10 个人入息课税比较引擎 v2（002 P1；Annex 005 事实契约）。

- v2 签名 `compare_personal_assessment(facts, bundle, *, availability=None)`：
  AvailabilityContext 为显式参数，缺 context 或与 bundle/engine/目标年不匹配
  → E_RULES_STATE_UNVERIFIABLE（无默认放行）。
- 候选：`no_pa`／`pa_self`／`pa_joint` 显式、非空、不隐式添加（no_pa 不是隐式
  候选）；P1–P5 真值表；`pa_self` 的 s.29 判断须看到 self 已 individual PA；
  mixed 共享单位无已批分配 → 仅该候选 unresolved（CROSS_MODE_UNIT_ALLOCATION_
  UNSUPPORTED）作未决竞争者阻 best；actual no_pa＋joint salaries 本阶段
  unsupported（不静默拆 separate）。
- Part 5 只降累进基数（s.42A）；标准径基数＝PA 减少后总入息（不减 Part 5）；
  取整按真实执行分支（pa_unit_floor_min_v1／pa_stc_comp_tp_v1）。
- 组件状态：PA 本阶段恒 partial、balance 恒 null、payment_comparison 范围外；
  best 仅依候选本年 final＋资格，unknown/unresolved 竞争者 → null；显式选中
  已知候选可保留 final。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Mapping

from app.core.errors import AppError
from app.core.facts_v2 import normalize_v2_facts
from app.core.money import ceil_at_point, floor_at_point
from app.engines.allowances import (
    AMOUNT_KEYS,
    Facts,
    add_years,
    analyze_marriage,
    canonical_amount,
    check_availability,
    collect_input_gates,
    dedupe_questions,
    exact_text,
    following_year,
    make_question,
    marriage_at,
    matrix_for,
    mpf_cap,
    node_amount,
    nonseparation_tri,
    previous_year,
    reduction_entry,
    resolve_claims,
    resolve_financials,
    salary_unit_tax,
    tf_at,
    tri_and,
    validate_ledger,
)
from app.rules.admission import VERIFIED_NO_PROFITS_REDUCTION_YEARS
# 已批五税数据区默认值已退役（A6：活动参数缺失＝规则不可核验，不得运行期回退）

_TAX_TYPE = "personal_assessment"
_PROFILE_ID = "part5_shared_v2"
_PA_CANDIDATES = ("no_pa", "pa_self", "pa_joint")
_PRIORITY = {"pa_self": 0, "pa_joint": 1, "no_pa": 2}


def compare_personal_assessment(
    facts: Mapping, bundle: Mapping, *, availability: object = None
) -> dict:
    """比较个人入息课税（pa_self／pa_joint）与不选择 PA（no_pa）候选（纯函数）。

    E2：结构规范化先于年度 guard 短路——全部显式 Year 先验（含候选/假设/
    引用/每键年 TF 域）在任何 availability 判定前完成。
    """
    if not isinstance(facts, Mapping):
        raise AppError("E_INPUT_TYPE", "facts 须为对象", field="facts")
    canonical_facts = normalize_v2_facts(facts, tax_type=_TAX_TYPE)
    year = canonical_facts["year_of_assessment"]
    ctx = check_availability(availability, bundle, year, tax_type=_TAX_TYPE)
    if ctx["current"].get("status") != "available":
        return _blocked_result(year, ctx["current"])
    facts_ctx = Facts(canonical_facts, bundle)
    run = _PARun(facts_ctx, ctx)
    return run.execute()


# ---------------------------------------------------------------------------
# 年度候选解析
# ---------------------------------------------------------------------------


class _PAYear:
    def __init__(
        self,
        bundle: Mapping,
        y: dict,
        subjects: Mapping[str, Any],
        *,
        for_next: bool = False,
        presence: list[dict] | None = None,
    ) -> None:
        self.bundle = bundle
        self.y = y
        self.subjects = subjects
        self.year = y["year"]
        self.for_next = for_next
        self.presence = list(presence or [])
        self.questions: list[dict] = []
        self.decisions: list[dict] = []
        self.assessments: list[dict] = []
        self.scenario_steps: list[dict] = []
        self.checks: list[dict] = []
        self.scenario_map: dict[str, dict] = {}
        self.finals: dict[str, int] = {}
        self.best: str | None = None
        self.selected: str | None = None
        self.has_unresolved = False
        self.all_ineligible = False
        self.shared_questions = 0
        self.candidate_question_spans: dict[str, tuple[int, int]] = {}
        self.input_unresolved = False

    # -- 主流程 --

    def compute(self) -> "_PAYear":
        y = self.y
        resolve_financials(self.bundle, y, self.subjects, self.questions)
        # E3：活动消费者前置（presence/来源/差饷/人工边界；见 allowances.
        # collect_input_gates）。PA 全部候选消费来源与组件，故未决即候选未决。
        gates = collect_input_gates(y, self.year)
        for question in gates["salary_questions"] + gates["component_questions"]:
            self.questions.append(question)
        self.input_unresolved = bool(
            gates["salary_questions"] or gates["component_questions"]
        )
        self.matrix = matrix_for(self.bundle, self.year)
        self.mpf_cap = mpf_cap(self.bundle, self.year)
        data = self.bundle.get("data") if isinstance(self.bundle, Mapping) else None
        pa_data = data.get("personal_assessment") if isinstance(data, Mapping) else None
        if not isinstance(pa_data, Mapping):
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                "规则束缺少 data.personal_assessment（活动参数不可核验，拒算）",
                field="data.personal_assessment",
            )
        self.pa_data = pa_data
        self.marriage = analyze_marriage(y)
        self.participants = [entry["person_id"] for entry in y["participants"]]
        self.self_id = y["self_id"]
        self.spouse_id = y.get("spouse_id")
        self.couple = len(self.participants) == 2 and self.spouse_id is not None
        validate_ledger(y, set(self.participants) if self.couple else None)
        # E6/§9.3：先按原始 hID/bID 识别同一资产/业务并核对共享原始值一致，
        # 再进入任何候选分配（不得按人各自计算后才对账）。
        self._validate_shared_raw_values()
        self.all_source = {
            pid: self._finance(pid).get("all_source") for pid in self.participants
        }
        self.ai = {
            pid: self._finance(pid).get("ai") for pid in self.participants
        }
        self.reference_date = y["assessment"]["reference_date"]
        # §4.10 规则 5：**仅 active actual** 才执行 reference_date 冲突检查
        # （hypothetical 取 reference_date；实际选举日期只用于检查既有选择/转换）。
        if (
            y["assessment"]["basis"] == "actual"
            and self.reference_date is not None
        ):
            for person_id, entry in y["assessment"]["pa_entries"].items():
                if (
                    entry["mode"] in ("individual", "joint")
                    and entry["date"] is not None
                    and entry["date"] != self.reference_date
                ):
                    raise AppError(
                        "E_INPUT_CONTRADICTORY",
                        "active actual PA 日期与 reference_date 不同且同时提交："
                        "必须报矛盾，不得静默择一（005 §4.10 规则 5）",
                        field=f"years.{self.year}.assessment.reference_date",
                    )
        # 候选集合按端点封闭（§9.3）：PA 端点仅 no_pa/pa_self/pa_joint
        for candidate_id in y["assessment"]["candidates"]:
            if candidate_id not in _PA_CANDIDATES:
                raise AppError(
                    "E_INPUT_EXTRA",
                    f"个人入息课税端点不支持的候选：{candidate_id!r}（§9.3）",
                    field=f"years.{self.year}.assessment.candidates",
                )
        self.shared_questions = len(self.questions)
        # §14/Approved005:738：候选按固定顺序（no_pa → pa_self → pa_joint）评估，
        # 不随请求数组次序（trace/checks/scenario 块顺序一致）。
        requested = set(y["assessment"]["candidates"])
        for candidate_id in [cid for cid in _PA_CANDIDATES if cid in requested]:
            question_start = len(self.questions)
            if candidate_id == "no_pa":
                self._eval_no_pa()
            elif candidate_id == "pa_self":
                self._eval_pa_self()
            else:
                self._eval_pa_joint()
            self.candidate_question_spans[candidate_id] = (
                question_start,
                len(self.questions),
            )
        self._select()
        return self

    def _select(self) -> None:
        eligible = [check for check in self.checks if check["admission"] == "eligible"]
        self.has_unresolved = any(
            check["admission"] == "unresolved" for check in self.checks
        )
        self.all_ineligible = bool(self.checks) and not eligible and not self.has_unresolved
        if self.has_unresolved:
            self.best = None
        elif eligible:
            self.best = min(
                eligible,
                key=lambda check: (
                    self.amount_of(check["candidate_id"]) or 0,
                    _PRIORITY.get(check["candidate_id"], 9),
                ),
            )["candidate_id"]
        else:
            self.best = None
        selection = self.y["assessment"]["selection"]
        self.selected = selection if selection != "best" else self.best

    def amount_of(self, candidate_id: str) -> int | None:
        """候选本年金额；暂缴投影（for_next）不套年度宽减（ADR-6）。"""
        scenario = self.scenario_map.get(candidate_id)
        if scenario is None:
            return None
        return scenario["before"] if self.for_next else scenario["final"]

    def selected_final(self) -> int | None:
        if self.selected is None:
            return None
        return self.amount_of(self.selected)

    # -- 辅助 --

    def _finance(self, person_id: str) -> dict:
        return self.y["financials"].get(person_id, {})

    def _new_check(self, candidate_id: str) -> dict:
        return {
            "candidate_id": candidate_id,
            "admission": None,
            "legal_path": None,
            "responsible_person_id": None,
            "separate_final": None,
            "conditional_final": None,
            "reason_codes": [],
        }

    def _unit_detail(
        self,
        scenario_id: str,
        unit_id: str,
        branch: str,
        actors: list[str],
        unit: dict,
        *,
        shares: list[dict] | None = None,
    ) -> dict:
        return {
            "year": self.year,
            "scenario_id": scenario_id,
            "unit_id": unit_id,
            "branch": branch,
            "actors": list(actors),
            "bases": unit["bases"],
            "operations": unit["operations"],
            "amounts": {
                "before_reduction": str(unit["before"]),
                "reduction": str(unit["reduction"]),
                "final_after_reduction": str(unit["final"]),
            },
            "shares": list(shares) if shares else [],
        }

    def _scenario_step(self, scenario_id: str, unit_ids: list[str], totals: dict) -> dict:
        return {
            "year": self.year,
            "scenario_id": scenario_id,
            "status": "partial",
            "unit_ids": list(unit_ids),
            "amounts": {
                "before_reduction": str(totals["before"]),
                "reduction": str(totals["reduction"]),
                "final_after_reduction": str(totals["final"]),
                "provisional_paid": (
                    canonical_amount(self.y["provisional_paid"])
                    if self.y["provisional_paid"] is not None
                    else None
                ),
                "next_provisional": None,
                "balance": None,
            },
        }

    def _p1_day(self, person_id: str) -> Any:
        """P1 求值日：hypothetical＝reference_date；actual＝实际选择日期（§4.10 规则 5/6）。"""
        if self.y["assessment"]["basis"] == "actual":
            entry = self.y["assessment"]["pa_entries"].get(person_id)
            if entry is None or entry["mode"] not in ("individual", "joint"):
                return None
            return entry["date"]
        return self.reference_date

    def _temporary_limb(self, person_id: str) -> Any:
        """s.41 temporary-resident 辅助日数：本 YA>180 或与相邻任一 YA 合计>300（严格大于）。"""
        days_map = {
            entry["year"]: entry["days"]
            for entry in self.presence
            if entry["person_id"] == person_id
        }
        current = days_map.get(self.year)
        if not isinstance(current, int):
            return None
        if current > 180:
            return True
        unknown = False
        for adjacent in (previous_year(self.year), following_year(self.year)):
            other = days_map.get(adjacent)
            if isinstance(other, int):
                if current + other > 300:
                    return True
            else:
                unknown = True
        return None if unknown else False

    def _p1_limb(self, person_id: str) -> Any:
        """P1＝(age≥18 OR 双亲已亡) AND (当日通常居港 OR temporary)（§9.2 P1）。

        E4：年龄肢三态 OR——both_parents_dead 缺失/unknown＝未证明（≠false）；
        生日缺失不阻止已证 both_dead 替代肢成立。
        """
        birth = self.subjects.get(person_id)
        day = self._p1_day(person_id)
        if day is None:
            return None
        facts = self.y["subject_facts"].get(person_id, {})
        dead_tf = facts.get("both_parents_dead", [])
        dead = tf_at(dead_tf, day) if dead_tf else None
        if birth is not None and day >= add_years(birth, 18):
            age_limb: Any = True
        elif dead is True:
            age_limb = True
        elif dead is None:
            age_limb = None
        else:
            # both_dead 已证 false：年龄肢仅由生日决定；生日缺失＝无法判定。
            age_limb = False if birth is not None else None
        resident = tf_at(facts.get("ordinarily_resident_hk", []), day)
        temporary = self._temporary_limb(person_id)
        if resident is True or temporary is True:
            residence: Any = True
        elif resident is False and temporary is False:
            residence = False
        else:
            residence = None
        return tri_and(age_limb, residence)

    def _p2_marriage_limb_detail(self) -> tuple[Any, str | None]:
        """P2 婚姻/非分居按候选参考/许可选择日求值（§9.2 P2；§4.10 规则 5/6）。

        返回 (limb, reason)：limb True/False/None；reason 用于 CF4 精确定位
        （"unmarried" / "apart" / "marriage-unknown" / "apart-unknown"）。
        """
        if self.y["assessment"]["basis"] == "actual":
            days = [
                entry["date"]
                for entry in self.y["assessment"]["pa_entries"].values()
                if entry["mode"] == "joint" and entry["date"] is not None
            ]
            day = days[0] if days else None
        else:
            day = self.reference_date
        if day is None:
            limb = True if self.marriage["state"] == "ordinary" else None
            return limb, (None if limb is not None else "marriage-unknown")
        value, _ = marriage_at(self.y["household"]["marriage"], day)
        if value is None:
            return None, "marriage-unknown"
        if value != "married":
            return False, "unmarried"
        apart = tf_at(self.y["household"]["living_apart"], day)
        if apart is True:
            return None, "apart"
        if apart is None:
            return None, "apart-unknown"
        return True, None

    def _p2_marriage_limb(self) -> Any:
        limb, _ = self._p2_marriage_limb_detail()
        return limb

    def _effective_mode(self, person_id: str) -> str | None:
        entry = self.y["assessment"]["pa_entries"].get(person_id)
        if entry is None:
            return None
        return entry["mode"]

    def _branch_a_recipient(self, effective: Mapping[str, str | None]) -> dict:
        if not self.couple:
            return {"recipient": None, "unresolved": None}
        state = self.marriage["state"]
        if state == "unknown":
            return {"recipient": None, "unresolved": "MARRIAGE_UNKNOWN"}
        if state in ("sequential", "s7"):
            # E4：S7 顺序配偶/分居变化下 s.29/婚姻归属决定不得默认分支 A。
            self.questions.append(
                make_question(
                    f"years.{self.year}.household.marriage",
                    self.year,
                    "同一课税年度内的顺序配偶/分居变化（S7）：s.29/婚姻归属决定"
                    "未决，不默认分支 A；请人工确认婚姻期间与配偶事实。",
                    subject_id=self.self_id,
                )
            )
            return {
                "recipient": None,
                "unresolved": self.marriage["reason"] or "S7",
            }
        if any(self.ai[pid] is None for pid in self.participants):
            return {"recipient": None, "unresolved": "AI_UNKNOWN"}
        ai_true = [pid for pid in self.participants if self.ai[pid] is True]
        ai_false = [pid for pid in self.participants if self.ai[pid] is False]
        if state == "unmarried" or len(ai_true) == 2:
            return {"recipient": None, "unresolved": None}
        if len(ai_false) == 1 and len(ai_true) == 1:
            false_pid = ai_false[0]
            mode = effective.get(false_pid)
            if mode is None or mode == "unknown":
                self.questions.append(
                    make_question(
                        f"years.{self.year}.assessment.actual.pa.{false_pid}",
                        self.year,
                        f"请确认 {false_pid} 的实际个人入息课税评税选择"
                        "（mode：none/individual/joint）；s.29 分支 A 判定需要该事实，"
                        "缺键不等于 none。",
                        subject_id=false_pid,
                    )
                )
                return {"recipient": None, "unresolved": "SPOUSE_PA_MODE_UNKNOWN"}
            if mode == "none":
                return {"recipient": ai_true[0], "unresolved": None}
        return {"recipient": None, "unresolved": None}

    # -- 薪俸单位（pa_stc_comp_tp_v1；no_pa 与 pa_self 的配偶侧共用） --

    def _compute_salary_units(
        self,
        scenario_id: str,
        effective: Mapping[str, str | None],
        *,
        unit_persons: list[str] | None = None,
    ) -> dict:
        allocation = self._branch_a_recipient(effective)
        if allocation["unresolved"]:
            return {"unresolved": allocation["unresolved"], "units": [], "totals": None}
        nomination = self._child_nomination()
        resolution = resolve_claims(
            y=self.y,
            subjects=self.subjects,
            matrix=self.matrix,
            year=self.year,
            window=self.y["window"],
            scenario_id=scenario_id,
            branch_a_recipient=allocation["recipient"],
            spouse_dda_holder=allocation["recipient"],
            child_nominee=nomination["holder"],
            child_nomination_unknown=nomination["unknown"],
            questions=self.questions,
        )
        self._parent_mutex_questions(resolution["parent_grants"])
        base_amounts: dict[str, int] = {pid: 0 for pid in self.participants}
        base_decisions: list[dict] = []
        if not self.couple:
            single_id = self.participants[0]
            base_amounts[single_id] = node_amount(self.matrix, "basic", self.year)
            base_decisions.append(
                self._allowance_decision(
                    scenario_id, "basic", single_id, base_amounts[single_id],
                    ["BASIC_ENTITLED"], ["s28"],
                )
            )
        elif allocation["recipient"] is not None:
            recipient = allocation["recipient"]
            base_amounts[recipient] = node_amount(self.matrix, "married", self.year)
            base_decisions.append(
                self._allowance_decision(
                    scenario_id, "married", recipient, base_amounts[recipient],
                    ["MARRIED_ENTITLED"], ["s29"],
                )
            )
        else:
            basic = node_amount(self.matrix, "basic", self.year)
            for pid in self.participants:
                base_amounts[pid] = basic
                base_decisions.append(
                    self._allowance_decision(
                        scenario_id, "basic", pid, basic, ["BASIC_ENTITLED"], ["s28"]
                    )
                )
        allowances = dict(base_amounts)
        for holder, amount in resolution["holder_amounts"].items():
            allowances[holder] = allowances.get(holder, 0) + amount
        persons = list(unit_persons) if unit_persons is not None else list(self.participants)
        units: list[dict] = []
        for pid in persons:
            finance = self._finance(pid)
            unit = salary_unit_tax(
                branch="pa_stc_comp_tp_v1",
                nai=finance.get("nai", Fraction(0)),
                part4a=min(finance.get("mpf", Fraction(0)), Fraction(self.mpf_cap))
                + finance.get("deductions", Fraction(0)),
                allowances=allowances.get(pid, 0),
                bands=self.pa_data["progressive_bands"],
                standard_bands=self.pa_data["standard_rate_bands"],
                reduction=reduction_entry(self.bundle, self.year),
            )
            units.append({"person_id": pid, "unit": unit})
        totals = {
            "before": sum(item["unit"]["before"] for item in units),
            "reduction": sum(item["unit"]["reduction"] for item in units),
            "final": sum(item["unit"]["final"] for item in units),
        }
        return {
            "unresolved": "CLAIM_UNRESOLVED" if resolution["unresolved"] else None,
            "units": units,
            "totals": totals,
            "holder_amounts": resolution["holder_amounts"],
            "decisions": base_decisions + resolution["decisions"],
            "base_amounts": base_amounts,
            "allowances": allowances,
            "allocation": allocation,
        }

    def _child_nomination(self) -> dict:
        if not self.couple:
            return {"holder": None, "unknown": False}
        nomination = self.y["household"]["child_nomination"]
        status = nomination["status"]
        nonseparation = nonseparation_tri(self.y)
        if nonseparation is False:
            # C1：已证分居 → s.31(3) 整组提名不适用，各 claim 归各自 claimant。
            return {"holder": None, "unknown": False}
        if nonseparation is None:
            # 非分居事实未知：不得按提名人自动归属（窄未决，定位婚姻/分居事实）。
            self.questions.append(
                make_question(
                    f"years.{self.year}.household.living_apart",
                    self.year,
                    "s.31(3) 整组提名例外须已证非分居：分居事实缺失/未知，"
                    "请确认婚姻期间的分居事实后重算。",
                )
            )
            return {"holder": None, "unknown": True}
        if status == "agreed":
            return {"holder": nomination["person_id"], "unknown": False}
        if status in ("unknown", "disputed"):
            return {"holder": None, "unknown": True}
        return {"holder": None, "unknown": False}

    def _parent_mutex_questions(self, parent_grants: list[tuple[str, str]]) -> None:
        for claimant, subject in parent_grants:
            for pid in self.participants:
                finance = self._finance(pid)
                for item in finance.get("deduction_items_resolved", []):
                    if subject not in item.get("subject_ids", []):
                        continue
                    if pid != claimant:
                        raise AppError(
                            "E_INPUT_CONTRADICTORY",
                            f"同一受养人 {subject} 的父母/祖父母免税额与长者住宿照顾"
                            "开支扣除跨人重复进入同一评税方案（005 §6.2）：无争议"
                            "事实必须拒绝，不得重复计入。",
                            field=f"years.{self.year}.financials.{pid}.deduction_items",
                        )
                    self.questions.append(
                        make_question(
                            f"years.{self.year}.financials.{claimant}.deduction_items",
                            self.year,
                            f"受养人 {subject} 的父母/祖父母免税额与长者住宿照顾开支"
                            "（elder_residential_care）扣除在同一评税方案内互斥"
                            "（s.30(5)）：请修正互斥意图（服务器不替用户选择）。",
                            subject_id=subject,
                        )
                    )

    # -- 物业/业务单位 --

    def _property_nav(self, property_id: str):
        for pid in self.participants:
            for prop in self._finance(pid).get("properties", []):
                if prop["property_id"] == property_id:
                    net_irrecoverable = max(
                        Fraction(0), prop["irrecoverable"] - prop["deposits"]
                    )
                    av = prop["rent"] - net_irrecoverable - prop["rates"]
                    repair = Fraction(1) - self._parse_ratio(
                        self._pa_value("repair_allowance_ratio")
                    )
                    return floor_at_point(av * repair)
        return 0

    def _parse_ratio(self, value: object) -> Fraction:
        from app.engines.allowances import parse_ratio

        return parse_ratio(value, "repair_allowance_ratio")

    def _pa_value(self, key: str) -> Any:
        """活动参数仅从实际传入束读取；缺失/非法 → E_RULES_STATE_UNVERIFIABLE（A6）。"""
        value = self.pa_data.get(key)
        if value is None:
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                f"个人入息课税比较缺少活动参数 {key}（缺键＝不可核验，拒算；"
                "不得回退发行模块默认值）。",
                field=f"data.personal_assessment.{key}",
            )
        return value

    def _property_units(self, pid: str, scenario_id: str) -> list[dict]:
        properties = self._finance(pid).get("properties", [])
        if not properties:
            return []
        units: list[dict] = []
        rate = self._parse_ratio(self._pa_value("property_standard_rate"))
        for prop in properties:
            nav = self._property_nav(prop["property_id"])
            tax_raw = Fraction(nav) * prop["share"] * rate
            before = floor_at_point(tax_raw)
            unit = {
                "branch": "property_per_assessment_floor_v1",
                "bases": [
                    {"name": "nav", "value": exact_text(Fraction(nav))},
                    {"name": "nav_share", "value": exact_text(Fraction(nav) * prop["share"])},
                ],
                "operations": [
                    {
                        "op": "floor",
                        "inputs": [exact_text(tax_raw)],
                        "output": exact_text(Fraction(before)),
                    }
                ],
                "before": before,
                "reduction": 0,
                "final": before,
            }
            units.append(
                {
                    "unit_id": f"property:{prop['property_id']}:{pid}",
                    "unit": unit,
                    "actors": [pid],
                }
            )
        return units

    def _property_units_shared(self) -> list[dict]:
        """E6/§9.3：先按原始 hID 识别单位并按每人份额汇总，**一个 hID 恰一个
        原始评税单位**（禁止先按人切片各自 floor）。单持有人沿用既有
        `property:<hID>:<pid>` 单位 ID；共同持有用 `property:<hID>`。
        """
        grouped: dict[str, list[tuple[str, dict]]] = {}
        for pid in self.participants:
            for prop in self._finance(pid).get("properties", []):
                grouped.setdefault(prop["property_id"], []).append((pid, prop))
        if not grouped:
            return []
        units: list[dict] = []
        rate = self._parse_ratio(self._pa_value("property_standard_rate"))
        for property_id, holders in grouped.items():
            nav = self._property_nav(property_id)
            total_share = sum((prop["share"] for _, prop in holders), Fraction(0))
            tax_raw = Fraction(nav) * total_share * rate
            before = floor_at_point(tax_raw)
            unit = {
                "branch": "property_per_assessment_floor_v1",
                "bases": [
                    {"name": "nav", "value": exact_text(Fraction(nav))},
                    {
                        "name": "nav_share",
                        "value": exact_text(Fraction(nav) * total_share),
                    },
                ],
                "operations": [
                    {
                        "op": "floor",
                        "inputs": [exact_text(tax_raw)],
                        "output": exact_text(Fraction(before)),
                    }
                ],
                "before": before,
                "reduction": 0,
                "final": before,
            }
            unit_id = (
                f"property:{property_id}:{holders[0][0]}"
                if len(holders) == 1
                else f"property:{property_id}"
            )
            units.append(
                {
                    "unit_id": unit_id,
                    "unit": unit,
                    "actors": [pid for pid, _ in holders],
                }
            )
        return units

    def _business_unit(self, unit_profit: Fraction) -> dict:
        """单一业务评税单位（approved_unincorporated_floor_branch_v1；T1 两级）"""
        tier = self._pa_value("profits_two_tier")
        try:
            threshold = Fraction(int(tier["threshold"]))
            low = Fraction(int(tier["low_rate_bp"]), 10000)
            high = Fraction(int(tier["high_rate_bp"]), 10000)
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                "活动参数 profits_two_tier 畸形（不可核验，拒算；不得裸异常）。",
                field="data.personal_assessment.profits_two_tier",
            ) from exc
        reductions = self.pa_data.get("profits_reductions")
        if not isinstance(reductions, Mapping):
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                "个人入息课税比较缺少活动参数 profits_reductions（缺键＝不可核验，"
                "拒算；不得回退发行模块默认值）。",
                field="data.personal_assessment.profits_reductions",
            )
        reduction = reductions.get(self.year)
        if (
            not isinstance(reduction, Mapping)
            and self.year not in VERIFIED_NO_PROFITS_REDUCTION_YEARS
        ):
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                f"活动利得宽减缺少 profits_reductions.{self.year}（缺键＝不可核验，"
                "拒算；不得按零宽减计算）。",
                field=f"data.personal_assessment.profits_reductions.{self.year}",
            )
        operations: list[dict] = []
        if floor_at_point(unit_profit) <= threshold:
            tax_raw = unit_profit * low
            operations.append(
                {
                    "op": "profit_low",
                    "inputs": [exact_text(unit_profit), exact_text(low)],
                    "output": exact_text(tax_raw),
                }
            )
        else:
            tax_raw = threshold * low + (unit_profit - threshold) * high
            operations.append(
                {
                    "op": "profit_high",
                    "inputs": [
                        exact_text(unit_profit),
                        exact_text(threshold),
                        exact_text(low),
                        exact_text(high),
                    ],
                    "output": exact_text(tax_raw),
                }
            )
        before = floor_at_point(tax_raw)
        operations.append(
            {
                "op": "floor",
                "inputs": [exact_text(tax_raw)],
                "output": exact_text(Fraction(before)),
            }
        )
        rebate = 0
        if isinstance(reduction, Mapping):
            rate = Fraction(int(reduction["percent_bp"]), 10000)
            cap = Fraction(int(reduction["cap"]))
            rebate = min(ceil_at_point(Fraction(before) * rate), floor_at_point(cap), before)
            operations.append(
                {
                    "op": "ceil",
                    "inputs": [exact_text(Fraction(before) * rate)],
                    "output": exact_text(Fraction(ceil_at_point(Fraction(before) * rate))),
                }
            )
        operations.append(
            {
                "op": "subtract",
                "inputs": [exact_text(Fraction(before)), exact_text(Fraction(rebate))],
                "output": exact_text(Fraction(before - rebate)),
            }
        )
        return {
            "branch": "approved_unincorporated_floor_branch_v1",
            "bases": [{"name": "profit_share", "value": exact_text(unit_profit)}],
            "operations": operations,
            "before": before,
            "reduction": rebate,
            "final": before - rebate,
        }

    def _business_units(self, pid: str, scenario_id: str) -> list[dict]:
        businesses = self._finance(pid).get("businesses", [])
        if not businesses:
            return []
        units: list[dict] = []
        for business in businesses:
            share_profit = business["profit"] * business["share"]
            units.append(
                {
                    "unit_id": f"business:{business['business_id']}:{pid}",
                    "unit": self._business_unit(share_profit),
                    "actors": [pid],
                }
            )
        return units

    def _business_units_shared(self) -> list[dict]:
        """先按原始 bID 识别同一业务/份额并核对共享原始值一致，再按单位计税一次（§9.3）"""
        grouped: dict[str, list[tuple[str, dict]]] = {}
        for pid in self.participants:
            for business in self._finance(pid).get("businesses", []):
                grouped.setdefault(business["business_id"], []).append((pid, business))
        units: list[dict] = []
        for business_id, holders in grouped.items():
            profits = {business["profit"] for _, business in holders}
            kinds = {business["kind"] for _, business in holders}
            if len(profits) > 1 or len(kinds) > 1:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"同一业务 {business_id} 的共同原始字段不一致"
                    "（assessable_profit/entity_kind 必须逐字一致，不得按人各自计算）",
                    field=f"years.{self.year}.financials.{business_id}",
                )
            total_share = sum(
                (business["share"] for _, business in holders), Fraction(0)
            )
            unit_profit = holders[0][1]["profit"] * total_share
            units.append(
                {
                    "unit_id": f"business:{business_id}",
                    "unit": self._business_unit(unit_profit),
                    "actors": [pid for pid, _ in holders],
                }
            )
        return units

    # -- PA 评税单位 --

    def _reduced_income(self, pid: str) -> Fraction:
        finance = self._finance(pid)
        total = finance.get("nai", Fraction(0))
        for prop in finance.get("properties", []):
            nav = self._property_nav(prop["property_id"])
            nav_share = Fraction(nav) * prop["share"]
            interest = min(prop["interest"], nav_share)
            total += nav_share - interest
        for business in finance.get("businesses", []):
            total += business["profit"] * business["share"]
        # E6：PA reduced income 接入 allowed 扣除（cap/原始事实后），非 requested。
        total -= finance.get("deductions", Fraction(0))
        return max(
            Fraction(0),
            total - min(finance.get("mpf", Fraction(0)), Fraction(self.mpf_cap)),
        )

    def _pa_unit(
        self,
        scenario_id: str,
        actor_ids: list[str],
        *,
        joint: bool,
        base_amount: int,
        claim_amount: int,
    ) -> dict:
        reduced = [self._reduced_income(pid) for pid in actor_ids]
        total_reduced = sum(reduced, Fraction(0))
        if joint:
            standard_base = total_reduced
            actors = list(actor_ids)
        else:
            standard_base = reduced[0]
            actors = [actor_ids[0]]
        allowances = base_amount + claim_amount
        unit = salary_unit_tax(
            branch="pa_unit_floor_min_v1",
            nai=standard_base,
            part4a=Fraction(0),
            allowances=allowances,
            bands=self.pa_data["progressive_bands"],
            standard_bands=self.pa_data["standard_rate_bands"],
            reduction=reduction_entry(self.bundle, self.year),
        )
        # E6/§14：真实 reduced-income 基数入 trace bases（allowed 扣除后）。
        unit["bases"] = list(unit["bases"]) + [
            {"name": "reduced_income", "value": exact_text(standard_base)}
        ]
        return {"unit": unit, "actors": actors, "reduced": reduced}

    # -- 候选：no_pa --

    def _eval_no_pa(self) -> dict:
        check = self._new_check("no_pa")
        assessment = self.y["assessment"]
        if self.input_unresolved:
            check["admission"] = "unresolved"
            check["reason_codes"] = ["INPUT_UNRESOLVED"]
            self.checks.append(check)
            return check
        # F4/R4（005 §4.10 依赖表第 5 行）：actual no_pa 的有效 salary 模式为
        # separate，须先核对 salary_joint_elected；缺失/unknown 不得由 PA
        # modes=none 派生 separate 成功。单身由既有 s.41(1B)/separate 控制覆盖。
        if (
            assessment["basis"] == "actual"
            and self.couple
            and assessment["salary_joint_elected"] not in (True, False)
        ):
            check["admission"] = "unresolved"
            check["reason_codes"] = ["ACTUAL_SALARY_JOINT_ELECTED_REQUIRED"]
            self.questions.append(
                make_question(
                    f"years.{self.year}.assessment.actual.salary_joint_elected",
                    self.year,
                    "actual 不选择 PA 的候选须核对实际薪俸模式：请确认实际未"
                    "选择联合薪俸评税（salary_joint_elected；缺失/unknown 不等于"
                    "未选）。",
                )
            )
            self.checks.append(check)
            return check
        if (
            assessment["basis"] == "actual"
            and (
                assessment["salary_joint_elected"] is True
                or assessment["salary_joint_assessed"] is True
            )
        ):
            check["admission"] = "unresolved"
            check["reason_codes"] = ["ACTUAL_NO_PA_JOINT_SALARY_UNSUPPORTED"]
            self.checks.append(check)
            return check
        effective = {pid: "none" for pid in self.participants}
        if assessment["basis"] == "actual":
            # E4：actual 选择/日期消费前置（缺键/unknown/缺 date 不静默当 none）
            gate = self._actual_pa_gate("no_pa")
            if gate is not None:
                check["admission"] = "unresolved"
                check["reason_codes"] = [gate["reason"]]
                self.questions.append(self._actual_selection_question(gate))
                self.checks.append(check)
                return check
            assessed_questions = self._actual_assessed_gate()
            if assessed_questions:
                check["admission"] = "unresolved"
                check["reason_codes"] = ["ACTUAL_SELECTION_STATE_UNRESOLVED"]
                self.questions.extend(assessed_questions)
                self.checks.append(check)
                return check
        # 与候选一致（P5）：actual 基线下已有 different mode 的选择 → unresolved
        if assessment["basis"] == "actual":
            for pid in self.participants:
                mode = self._effective_mode(pid)
                if mode not in (None, "none", "unknown"):
                    check["admission"] = "unresolved"
                    check["reason_codes"] = ["ACTUAL_PA_MODE_CONFLICT"]
                    self.checks.append(check)
                    return check
        salary = self._compute_salary_units("no_pa", effective)
        if salary["unresolved"]:
            check["admission"] = "unresolved"
            check["reason_codes"] = [salary["unresolved"]]
            self.checks.append(check)
            return check
        self.decisions.extend(salary["decisions"])
        self.decisions.extend(self._deduction_decisions("no_pa"))
        unit_entries: list[dict] = [
            {
                "unit_id": f"salary:{item['person_id']}",
                "unit": item["unit"],
                "actors": [item["person_id"]],
            }
            for item in salary["units"]
        ]
        unit_entries.extend(self._property_units_shared())
        unit_entries.extend(self._business_units_shared())
        # §14：单位 ID 升序（同一 scenario 块内规定顺序）。
        unit_entries.sort(key=lambda entry: entry["unit_id"])
        for entry in unit_entries:
            actors = entry.get("actors") or [entry["unit_id"].split(":")[-1]]
            self.assessments.append(
                self._unit_detail(
                    "no_pa",
                    entry["unit_id"],
                    entry["unit"]["branch"],
                    actors,
                    entry["unit"],
                )
            )
        totals = {
            "before": sum(entry["unit"]["before"] for entry in unit_entries),
            "reduction": sum(entry["unit"]["reduction"] for entry in unit_entries),
            "final": sum(entry["unit"]["final"] for entry in unit_entries),
        }
        self.scenario_steps.append(
            self._scenario_step("no_pa", [entry["unit_id"] for entry in unit_entries], totals)
        )
        self.scenario_map["no_pa"] = totals
        self.finals["no_pa"] = totals["final"]
        check["admission"] = "eligible"
        check["legal_path"] = "pa"
        check["reason_codes"] = ["NO_PA_SEPARATE_UNITS"]
        self.checks.append(check)
        return check

    # -- 候选：pa_self --

    def _eval_pa_self(self) -> dict:
        check = self._new_check("pa_self")
        assessment = self.y["assessment"]
        if self.input_unresolved:
            check["admission"] = "unresolved"
            check["reason_codes"] = ["INPUT_UNRESOLVED"]
            self.checks.append(check)
            return check
        # P3：s.41(1B) 已联合薪俸评税 → 单独 PA 被禁
        if assessment["salary_joint_assessed"] is True:
            check["admission"] = "ineligible"
            check["reason_codes"] = ["S41_1B_JOINT_SALARY_BLOCKS_SINGLE_PA"]
            self.checks.append(check)
            return check
        # E4：actual 选择/日期消费前置（缺键/unknown/缺 date＝需补事实，不静默）
        gate = self._actual_pa_gate("pa_self")
        if gate is not None:
            check["admission"] = "unresolved"
            check["reason_codes"] = [gate["reason"]]
            self.questions.append(self._actual_selection_question(gate))
            self.checks.append(check)
            return check
        if assessment["basis"] == "actual":
            actual_gate_questions = (
                self._actual_history_gate() + self._actual_assessed_gate()
            )
            if actual_gate_questions:
                check["admission"] = "unresolved"
                check["reason_codes"] = ["ACTUAL_SELECTION_STATE_UNRESOLVED"]
                self.questions.extend(actual_gate_questions)
                self.checks.append(check)
                return check
        if assessment["basis"] == "hypothetical" and self.reference_date is None:
            check["admission"] = "unresolved"
            check["reason_codes"] = ["REFERENCE_DATE_REQUIRED"]
            self.checks.append(check)
            return check
        # P5：与候选有效模式一致性（actual 严格；hypothetical 仅既有选择冲突）
        expected = {self.self_id: "individual"}
        if self.couple:
            expected[self.spouse_id] = "none"
        conflict = self._pa_mode_conflict(
            expected=expected,
            strict=assessment["basis"] == "actual",
        )
        if conflict is not None:
            check["admission"] = "unresolved"
            check["reason_codes"] = [conflict]
            self.checks.append(check)
            return check
        # mixed 共享单位：同一 hID/bID 横跨 PA actor 与 non-PA actor → 仅该候选 unsupported
        if self._has_shared_units():
            check["admission"] = "unresolved"
            check["reason_codes"] = ["CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED"]
            self.checks.append(check)
            return check
        p1 = self._p1_limb(self.self_id)
        if p1 is False:
            check["admission"] = "ineligible"
            check["reason_codes"] = ["P1_NOT_MET"]
            self.checks.append(check)
            return check
        if p1 is None:
            check["admission"] = "unresolved"
            check["reason_codes"] = ["P1_UNKNOWN"]
            self.questions.append(
                make_question(
                    f"years.{self.year}.subject_facts.{self.self_id}.ordinarily_resident_hk",
                    self.year,
                    f"请确认 {self.self_id} 的年龄/居港肢（P1）事实——PA 候选合法性需要。",
                    subject_id=self.self_id,
                )
            )
            self.checks.append(check)
            return check
        # 生效模式：self=individual（候选假设）；spouse=none
        effective = {pid: "none" for pid in self.participants}
        effective[self.self_id] = "individual"
        salary = self._compute_salary_units(
            "pa_self",
            effective,
            unit_persons=[pid for pid in self.participants if pid != self.self_id],
        )
        if salary["unresolved"]:
            check["admission"] = "unresolved"
            check["reason_codes"] = [salary["unresolved"]]
            self.checks.append(check)
            return check
        self.decisions.extend(salary["decisions"])
        self.decisions.extend(self._deduction_decisions("pa_self"))
        self_unit = self._pa_unit(
            "pa_self",
            [self.self_id],
            joint=False,
            base_amount=salary["base_amounts"].get(self.self_id, 0),
            claim_amount=salary["holder_amounts"].get(self.self_id, 0),
        )
        unit_entries: list[dict] = [
            {
                "unit_id": f"pa:{self.self_id}",
                "unit": self_unit["unit"],
                "actors": [self.self_id],
            }
        ] + [
            {
                "unit_id": f"salary:{item['person_id']}",
                "unit": item["unit"],
                "actors": [item["person_id"]],
            }
            for item in salary["units"]
        ]
        for pid in self.participants:
            if pid == self.self_id:
                continue
            unit_entries.extend(self._property_units(pid, "pa_self"))
            unit_entries.extend(self._business_units(pid, "pa_self"))
        # §14：单位 ID 升序（同一 scenario 块内规定顺序）。
        unit_entries.sort(key=lambda entry: entry["unit_id"])
        for entry in unit_entries:
            actors = entry.get("actors") or [entry["unit_id"].split(":")[-1]]
            self.assessments.append(
                self._unit_detail(
                    "pa_self",
                    entry["unit_id"],
                    entry["unit"]["branch"],
                    actors,
                    entry["unit"],
                )
            )
        totals = {
            "before": sum(entry["unit"]["before"] for entry in unit_entries),
            "reduction": sum(entry["unit"]["reduction"] for entry in unit_entries),
            "final": sum(entry["unit"]["final"] for entry in unit_entries),
        }
        self.scenario_steps.append(
            self._scenario_step("pa_self", [entry["unit_id"] for entry in unit_entries], totals)
        )
        self.scenario_map["pa_self"] = totals
        self.finals["pa_self"] = totals["final"]
        check["admission"] = "eligible"
        check["legal_path"] = "pa"
        check["reason_codes"] = ["PA_SELF_INDIVIDUAL_ELECTION"]
        self.checks.append(check)
        return check

    def _actual_pa_gate(self, candidate_id: str) -> dict | None:
        """actual 基线的选择/日期消费前置（§4.10 规则 3/5/7）。

        返回 None＝通过；否则 {"reason", "pid"}——缺键≠none、unknown 不跳过、
        日期消费者活动时 individual/joint 缺 date 须补事实（不静默择一）。
        仅 actual；hypothetical 由 reference_date 路径负责。
        """
        if self.y["assessment"]["basis"] != "actual":
            return None
        entries = self.y["assessment"]["pa_entries"]
        if candidate_id == "no_pa":
            required = {pid: "none" for pid in self.participants}
        elif candidate_id == "pa_self":
            # F4/R4（005 §4.10 规则 7＋§9.3 pa_self 行）：self=individual 与
            # 实际 spouse=none 是同一完整期望；配偶条目缺键/unknown 不得由
            # 下游派生 none 掩盖。
            required = {self.self_id: "individual"}
            if self.couple:
                required[self.spouse_id] = "none"
        else:
            required = {pid: "joint" for pid in self.participants}
        for pid, want in required.items():
            entry = entries.get(pid)
            if entry is None:
                return {"reason": "ACTUAL_PA_MODE_MISSING", "pid": pid}
            if entry["mode"] == "unknown":
                return {"reason": "ACTUAL_PA_MODE_UNKNOWN", "pid": pid}
            if (
                entry["mode"] == want
                and entry["mode"] in ("individual", "joint")
                and entry["date"] is None
            ):
                return {"reason": "ACTUAL_PA_DATE_MISSING", "pid": pid}
        return None

    def _actual_selection_question(self, gate: Mapping) -> dict:
        return make_question(
            f"years.{self.year}.assessment.actual.pa.{gate['pid']}",
            self.year,
            "actual 基线的个人入息课税选择（mode/date）未确定：缺键/unknown 不等于"
            " none，active 日期消费者需要该选择日；请补全后重算。",
            subject_id=gate["pid"],
        )

    def _actual_history_gate(self) -> list[dict]:
        """active actual PA 的历史三态（§4.10 依赖表）：missing/unknown → 追问；
        已证非 ordinary（withdrawn/disputed）→ 未决不追问。

        仅**夫妻选择上下文**（含配偶参与者）消费 history；单身实际选择
        由 s.10(5)/既有控制覆盖（age_boundary 冻结控制）。"""
        if not self.couple:
            return []
        if self.y["assessment"]["basis"] != "actual":
            return []
        history = self.y["assessment"].get("history")
        if history == "ordinary":
            return []
        field = f"years.{self.year}.assessment.actual.history"
        if history is None or history == "unknown":
            return [
                make_question(
                    field,
                    self.year,
                    "actual 基线的选择历史状态（history）缺失/unknown：请确认"
                    "ordinary/withdrawn/disputed 后重算。",
                    subject_id=self.self_id,
                )
            ]
        return [
            make_question(
                field,
                self.year,
                "actual 基线的选择历史状态非 ordinary：相关候选未决。",
                subject_id=self.self_id,
            )
        ]

    def _actual_assessed_gate(self) -> list[dict]:
        """salary_joint_assessed（s.41(1B) 依赖核对）在 actual 基线须显式。

        仅夫妻选择上下文（含配偶参与者）消费；单身由 s.41(1B) 既有路径覆盖。"""
        if not self.couple:
            return []
        if self.y["assessment"]["basis"] != "actual":
            return []
        assessed = self.y["assessment"].get("salary_joint_assessed")
        if assessed in (True, False):
            return []
        return [
            make_question(
                f"years.{self.year}.assessment.actual.salary_joint_assessed",
                self.year,
                "salary_joint_assessed 缺失/unknown：s.41(1B) 模式核对未决"
                "（缺失不等于 false），请补全后重算。",
                subject_id=self.self_id,
            )
        ]

    def _pa_mode_conflict(
        self, expected: Mapping[str, str], *, strict: bool
    ) -> str | None:
        entries = self.y["assessment"]["pa_entries"]
        for pid, entry in entries.items():
            want = expected.get(pid)
            if want is None:
                continue
            if entry["mode"] == want or entry["mode"] == "unknown":
                continue
            if strict:
                return "ACTUAL_PA_MODE_CONFLICT"
            if entry["mode"] in ("individual", "joint"):
                return "ACTUAL_PA_MODE_CONFLICT"
        return None

    def _has_shared_units(self) -> bool:
        if not self.couple:
            return False
        seen: dict[str, set[str]] = {}
        for pid in self.participants:
            finance = self._finance(pid)
            for prop in finance.get("properties", []):
                seen.setdefault(f"property:{prop['property_id']}", set()).add(pid)
            for business in finance.get("businesses", []):
                seen.setdefault(f"business:{business['business_id']}", set()).add(pid)
        return any(len(owners) > 1 for owners in seen.values())

    def _validate_shared_raw_values(self) -> None:
        """E6/§9.3/§4.6：同 hID/bID 的共同原始值须逐字一致（候选分配前）。

        物业共享原始＝rent_received／rates_paid_by_owner／irrecoverable_rent／
        deposit_offsets（份额与个人利息可差异属合法）；业务共享原始＝
        assessable_profit／entity_kind。仅单持有人时无对账对象。
        """
        property_groups: dict[str, list[tuple[str, dict]]] = {}
        business_groups: dict[str, list[tuple[str, dict]]] = {}
        for pid in self.participants:
            finance = self._finance(pid)
            for prop in finance.get("properties", []):
                property_groups.setdefault(prop["property_id"], []).append((pid, prop))
            for business in finance.get("businesses", []):
                business_groups.setdefault(business["business_id"], []).append(
                    (pid, business)
                )
        for property_id, holders in property_groups.items():
            shared = {
                (
                    prop["rent"],
                    prop["rates_raw"],
                    prop["irrecoverable"],
                    prop["deposits"],
                )
                for _, prop in holders
            }
            if len(shared) > 1:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"同一物业 {property_id} 的共同原始字段不一致（rent_received／"
                    "rates_paid_by_owner／irrecoverable_rent／deposit_offsets 须逐字"
                    "一致；份额/个人利息可不同，005 §4.6/§9.3）",
                    field=f"years.{self.year}.financials.{property_id}",
                )
        for business_id, holders in business_groups.items():
            shared_business = {(item["profit"], item["kind"]) for _, item in holders}
            if len(shared_business) > 1:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"同一业务 {business_id} 的共同原始字段不一致"
                    "（assessable_profit/entity_kind 必须逐字一致，005 §9.3）",
                    field=f"years.{self.year}.financials.{business_id}",
                )

    def _allowance_decision(
        self,
        scenario_id: str,
        item_kind: str,
        claimant_id: str,
        amount: int,
        reason_codes: list[str],
        rule_refs: list[str],
    ) -> dict:
        """基础额（basic/married）decision（§14 示例形状）。

        claim_id：PA 基础额为服务器派生、无对应输入 claim；取归属人的稳定
        person id 作为可排序标识（§14 允许 null|string；trace 排序可判）。
        """
        return {
            "year": self.year,
            "scenario_id": scenario_id,
            "claim_id": claimant_id,
            "claimant_id": claimant_id,
            "subject_id": claimant_id,
            "item_kind": item_kind,
            "entitlement": "eligible",
            "grant": "allowed",
            "amount": str(amount),
            "reason_codes": sorted(set(reason_codes)),
            "rule_refs": sorted(set(rule_refs)),
        }

    def _deduction_decisions(self, scenario_id: str) -> list[dict]:
        """Part 4A/PA 扣除 decision 逐条入 steps（requested≠allowed；§5.1/§14）。

        requested＋raw 原始资格不成立（RAW_FACTS_NOT_MET）→ ineligible（与
        excluded 的 not_requested 区分）；未决 → unresolved/unknown。
        C4/R-E56：allowed="0" 且无未决原因码＝已解析零额（cap 用尽等），不得
        与「资格未知」混同（资格成立、金额 0）。
        """
        rows: list[dict] = []
        for person_id in self.participants:
            finance = self._finance(person_id)
            for row in finance.get("deduction_decisions", []):
                allowed = row.get("allowed")
                requested = row.get("requested")
                reasons = set(row.get("reason_codes") or [])
                if allowed not in (None, "0"):
                    entitlement, grant, amount = "eligible", "allowed", allowed
                elif requested is False:
                    entitlement, grant, amount = "ineligible", "not_requested", None
                elif requested is True and "RAW_FACTS_NOT_MET" in reasons:
                    entitlement, grant, amount = "ineligible", "ineligible", None
                elif requested is True and "RAW_FACTS_UNKNOWN" not in reasons:
                    # 已解析的零额（如 cap 用尽）：资格成立、金额 0。
                    entitlement, grant, amount = "eligible", "allowed", "0"
                else:
                    entitlement, grant, amount = "unknown", "unresolved", None
                rows.append(
                    {
                        "year": self.year,
                        "scenario_id": scenario_id,
                        "claim_id": None,
                        "claimant_id": person_id,
                        "subject_id": row.get("subject_id"),
                        "item_kind": row.get("item_type"),
                        "entitlement": entitlement,
                        "grant": grant,
                        "amount": amount,
                        "reason_codes": sorted(reasons),
                        "rule_refs": [],
                    }
                )
        return rows

    # -- 候选：pa_joint --

    def _eval_pa_joint(self) -> dict:
        check = self._new_check("pa_joint")
        assessment = self.y["assessment"]
        if self.input_unresolved:
            check["admission"] = "unresolved"
            check["reason_codes"] = ["INPUT_UNRESOLVED"]
            self.checks.append(check)
            return check
        if not self.couple:
            check["admission"] = "ineligible"
            check["reason_codes"] = ["JOINT_PA_REQUIRES_COUPLE"]
            self.checks.append(check)
            return check
        state = self.marriage["state"]
        if state == "unknown":
            check["admission"] = "unresolved"
            check["reason_codes"] = [self.marriage["reason"] or "MARRIAGE_UNKNOWN"]
            self.checks.append(check)
            return check
        if state == "unmarried":
            check["admission"] = "ineligible"
            check["reason_codes"] = ["NOT_MARRIED"]
            self.checks.append(check)
            return check
        if assessment["basis"] == "actual":
            # E4：actual 配对选择/日期消费前置（joint 双方条目与日期）
            gate = self._actual_pa_gate("pa_joint")
            if gate is not None:
                check["admission"] = "unresolved"
                check["reason_codes"] = [gate["reason"]]
                self.questions.append(self._actual_selection_question(gate))
                self.checks.append(check)
                return check
            actual_gate_questions = (
                self._actual_history_gate() + self._actual_assessed_gate()
            )
            if actual_gate_questions:
                check["admission"] = "unresolved"
                check["reason_codes"] = ["ACTUAL_SELECTION_STATE_UNRESOLVED"]
                self.questions.extend(actual_gate_questions)
                self.checks.append(check)
                return check
            # CF4：选择日已知反证（未婚/分居）→ 仅相依 joint 候选 unresolved＋定位；
            # 不整份矛盾、不回退默认（005 §9.2 P5 段）。
            p2_limb, p2_reason = self._p2_marriage_limb_detail()
            if p2_limb is not True:
                check["admission"] = "unresolved"
                check["reason_codes"] = [
                    "P2_MARRIAGE_UNKNOWN" if p2_limb is None else "P2_MARRIAGE_NOT_MET"
                ]
                field = (
                    f"years.{self.year}.household.living_apart"
                    if p2_reason in ("apart", "apart-unknown")
                    else f"years.{self.year}.household.marriage"
                )
                self.questions.append(
                    make_question(
                        field,
                        self.year,
                        "pa_joint 的婚姻/非分居须按候选参考/许可选择日求值："
                        "该日已知未婚/分居或事实未知——仅相依 joint 候选未决，"
                        "不整份拒算（005 §9.2 P5）。",
                    )
                )
                self.checks.append(check)
                return check
        if state in ("sequential", "s7"):
            check["admission"] = "unresolved"
            check["reason_codes"] = [self.marriage["reason"] or "MARRIAGE_UNKNOWN"]
            self.checks.append(check)
            return check
        if assessment["basis"] == "hypothetical" and self.reference_date is None:
            check["admission"] = "unresolved"
            check["reason_codes"] = ["REFERENCE_DATE_REQUIRED"]
            self.checks.append(check)
            return check
        if assessment["basis"] == "hypothetical":
            p2_limb = self._p2_marriage_limb()
            if p2_limb is not True:
                check["admission"] = "unresolved" if p2_limb is None else "ineligible"
                check["reason_codes"] = [
                    "P2_MARRIAGE_UNKNOWN" if p2_limb is None else "P2_MARRIAGE_NOT_MET"
                ]
                if p2_limb is None:
                    self.questions.append(
                        make_question(
                            f"years.{self.year}.household.marriage",
                            self.year,
                            "pa_joint 的婚姻/非分居须按候选参考/许可选择日求值：请补全"
                            "该日的婚姻与分居事实（未知不得默认已婚）。",
                        )
                    )
                self.checks.append(check)
                return check
        for pid in self.participants:
            if self.all_source[pid] is False:
                check["admission"] = "ineligible"
                check["reason_codes"] = ["P2_ALL_SOURCE_NOT_MET"]
                self.checks.append(check)
                return check
            if self.all_source[pid] is None:
                check["admission"] = "unresolved"
                check["reason_codes"] = ["P2_ALL_SOURCE_UNKNOWN"]
                self.checks.append(check)
                return check
        conflict = self._pa_mode_conflict(
            expected={pid: "joint" for pid in self.participants},
            strict=assessment["basis"] == "actual",
        )
        if conflict is not None:
            check["admission"] = "unresolved"
            check["reason_codes"] = [conflict]
            self.checks.append(check)
            return check
        limbs = [self._p1_limb(pid) for pid in self.participants]
        if any(limb is True for limb in limbs):
            pass
        elif any(limb is None for limb in limbs):
            check["admission"] = "unresolved"
            check["reason_codes"] = ["P1_UNKNOWN"]
            self.checks.append(check)
            return check
        else:
            check["admission"] = "ineligible"
            check["reason_codes"] = ["P1_NOT_MET"]
            self.checks.append(check)
            return check
        nomination = self._child_nomination()
        resolution = resolve_claims(
            y=self.y,
            subjects=self.subjects,
            matrix=self.matrix,
            year=self.year,
            window=self.y["window"],
            scenario_id="pa_joint",
            branch_a_recipient=None,
            spouse_dda_holder=None,
            child_nominee=nomination["holder"],
            child_nomination_unknown=nomination["unknown"],
            questions=self.questions,
            spouse_dda_both=True,
        )
        if resolution["unresolved"]:
            check["admission"] = "unresolved"
            check["reason_codes"] = ["CLAIM_UNRESOLVED"]
            self.checks.append(check)
            return check
        self._parent_mutex_questions(resolution["parent_grants"])
        self.decisions.append(
            self._allowance_decision(
                "pa_joint",
                "married",
                self.self_id,
                node_amount(self.matrix, "married", self.year),
                ["MARRIED_ENTITLED"],
                ["s29"],
            )
        )
        self.decisions.extend(resolution["decisions"])
        self.decisions.extend(self._deduction_decisions("pa_joint"))
        claim_amount = sum(resolution["holder_amounts"].values())
        pa_unit = self._pa_unit(
            "pa_joint",
            list(self.participants),
            joint=True,
            base_amount=node_amount(self.matrix, "married", self.year),
            claim_amount=claim_amount,
        )
        reduced = pa_unit["reduced"]
        total_reduced = sum(reduced, Fraction(0))
        # s.43(2B)/005 §14：joint 宽减后按 Ri/ΣR 记录精确比例；ΣR=0 不除法。
        shares = (
            [
                {"person_id": pid, "ratio": exact_text(value / total_reduced)}
                for pid, value in zip(pa_unit["actors"], reduced)
            ]
            if total_reduced > 0
            else []
        )
        self.assessments.append(
            self._unit_detail(
                "pa_joint",
                "pa:joint",
                "pa_unit_floor_min_v1",
                list(self.participants),
                pa_unit["unit"],
                shares=shares,
            )
        )
        unit_entries = [{"unit_id": "pa:joint", "unit": pa_unit["unit"]}]
        totals = {
            "before": pa_unit["unit"]["before"],
            "reduction": pa_unit["unit"]["reduction"],
            "final": pa_unit["unit"]["final"],
        }
        self.scenario_steps.append(
            self._scenario_step("pa_joint", [entry["unit_id"] for entry in unit_entries], totals)
        )
        self.scenario_map["pa_joint"] = totals
        self.finals["pa_joint"] = totals["final"]
        check["admission"] = "eligible"
        check["legal_path"] = "pa"
        check["reason_codes"] = ["JOINT_PA_ELECTION"]
        self.checks.append(check)
        return check


# ---------------------------------------------------------------------------
# 目标年运行（信封、下一年、steps）
# ---------------------------------------------------------------------------


class _PARun:
    def __init__(self, facts: Facts, availability: Mapping) -> None:
        self.facts = facts
        self.availability = availability
        self.year = facts.year
        self.next_amount: int | None = None
        self.next_selected: str | None = None
        self.next_reason: str | None = None

    def execute(self) -> dict:
        year_view = _PAYear(
            self.facts.bundle,
            self.facts.y,
            self.facts.subjects,
            presence=self.facts.presence,
        ).compute()
        self.view = year_view
        self._compute_next()
        questions = dedupe_questions(year_view.questions)
        checks = year_view.checks
        eligible = [check for check in checks if check["admission"] == "eligible"]
        selected_final = year_view.selected_final()
        paid = self.facts.y["provisional_paid"]
        # 候选级问题与结果级状态分离（R7.1）：已知 selected 保留 final。
        if year_view.selected is not None:
            span = year_view.candidate_question_spans.get(year_view.selected)
            own_questions = year_view.shared_questions + (
                (span[1] - span[0]) if span is not None else 0
            )
        else:
            own_questions = len(questions)
        if year_view.all_ineligible:
            status = "blocked"
        elif selected_final is not None:
            status = "needs_input" if own_questions > 0 else "partial"
        elif year_view.selected is not None:
            chosen = next(
                (
                    check
                    for check in checks
                    if check["candidate_id"] == year_view.selected
                ),
                None,
            )
            if chosen is not None and chosen["admission"] == "unresolved":
                status = "needs_input"
            elif year_view.all_ineligible:
                status = "blocked"
            else:
                status = "partial"
        elif eligible:
            status = "partial"
        else:
            status = "needs_input"
        if status == "needs_input" and not questions:
            first = checks[0] if checks else None
            reason = (first or {}).get("reason_codes") or ["UNRESOLVED"]
            questions.append(
                make_question(
                    f"years.{self.year}.assessment",
                    self.year,
                    f"所列候选无法确定（原因标签：{'/'.join(map(str, reason))}）："
                    "请补全所需事实。",
                )
            )
        amounts = self._amounts(status, year_view, selected_final, paid)
        self._complete_scenario_trace(year_view, status, amounts)
        result = {
            "status": status,
            "amounts": amounts,
            "best_scenario": year_view.best,
            "questions": questions,
            "steps": self._steps(year_view),
            "warnings": [],
            "unsupported": [],
            "pending_verification": [],
            "missing_components": self._missing_components(paid, year_view),
        }
        if status == "blocked":
            result["blocked_reason"] = "所列候选全部明确不合资格（P4）：不产确定税额"
        return result

    def _complete_scenario_trace(
        self, view: _PAYear, status: str, amounts: Mapping
    ) -> None:
        """组件确定后完成 trace：selected scenario 六键与 top-level 对账（§10/§14）。

        next/balance 不得恒 null；needs_input/blocked 不覆写已存 scenario 现场。
        """
        if view.selected is None or status in ("needs_input", "blocked"):
            return
        for detail in view.scenario_steps:
            if detail.get("scenario_id") != view.selected:
                continue
            detail["status"] = status
            detail["amounts"] = dict(amounts)

    def _amounts(
        self,
        status: str,
        view: _PAYear,
        selected_final: int | None,
        paid: Fraction | None,
    ) -> dict:
        if status in ("needs_input", "blocked"):
            return {key: None for key in AMOUNT_KEYS}
        paid_text = canonical_amount(paid) if paid is not None else None
        next_text = (
            canonical_amount(Fraction(self.next_amount))
            if self.next_amount is not None
            else None
        )
        scenario = (
            view.scenario_map.get(view.selected) if view.selected is not None else None
        )
        if scenario is None:
            return {
                "before_reduction": None,
                "reduction": None,
                "final_after_reduction": None,
                "provisional_paid": paid_text,
                "next_provisional": next_text,
                "balance": None,
            }
        return {
            "before_reduction": str(scenario["before"]),
            "reduction": str(scenario["reduction"]),
            "final_after_reduction": str(scenario["final"]),
            "provisional_paid": paid_text,
            "next_provisional": next_text,
            "balance": None,  # PA 恒 null（跨税项比较属阶段 3）
        }

    def _missing_components(self, paid: Fraction | None, view: _PAYear) -> list[str]:
        missing: list[str] = []
        if paid is None:
            missing.append("provisional_paid")
        if self.next_amount is None:
            missing.append(f"next_provisional:{self.facts.following}")
        for check in view.checks:
            if check["admission"] == "unresolved":
                missing.append(f"candidate:{check['candidate_id']}")
        return missing

    def _compute_next(self) -> None:
        try:
            next_y = self.facts.build_next()
        except AppError as exc:
            if exc.code == "E_RULES_STATE_UNVERIFIABLE":
                self.next_reason = exc.code
                return
            # 显式下一年输入的非法类型/extra/已证矛盾仍拒绝（不被年度隔离吞掉）
            raise
        if next_y is not None:
            participants = [entry["person_id"] for entry in next_y["participants"]]
            couple = len(participants) == 2 and next_y.get("spouse_id") is not None
            # guard 短路不得越过输入对账（复核 §四.2）
            validate_ledger(next_y, set(participants) if couple else None)
        upcoming = self.availability["next"]
        if upcoming.get("status") != "available":
            self.next_reason = f"TARGET_RULES_{str(upcoming.get('status')).upper()}"
            return
        if next_y is None:
            self.next_reason = "TARGET_FACTS_MISSING"
            return
        try:
            view = _PAYear(
                self.facts.bundle,
                next_y,
                self.facts.subjects,
                for_next=True,
                presence=self.facts.presence,
            ).compute()
        except AppError as exc:
            if exc.code == "E_RULES_STATE_UNVERIFIABLE":
                # E7：next 活动规则缺键/畸形 → 年度隔离（仅抑 next）；
                # 其余 AppError（含 E_INPUT_*／E_DEDUCTION_UNSUPPORTED）
                # 照旧重抛，不用 catch-all 吞错。
                self.next_reason = exc.code
                return
            raise
        if view.questions or view.all_ineligible:
            self.next_reason = "TARGET_FACTS_INCOMPLETE"
            return
        final = view.selected_final()
        if final is None:
            self.next_reason = "TARGET_UNRESOLVED"
            return
        self.next_amount = final
        self.next_selected = view.selected

    def _steps(self, view: _PAYear) -> list[dict]:
        steps: list[dict] = [
            {
                "label": (
                    f"{_year_label(self.year)} 个人入息课税（税率与取整口径："
                    "累进税率带与标准税率上限两径分别 floor 后择低，宽减 ceil "
                    "后扣）"
                ),
                "kind": "context",
                "detail": {
                    "year": self.year,
                    "tax_type": _TAX_TYPE,
                    "profile_id": _PROFILE_ID,
                    "assumptions": [
                        {
                            "assumption_id": item["assumption_id"],
                            "kind": item["kind"],
                            "person_id": item["person_id"],
                            "source_year": item["source_year"],
                            "target_year": item["target_year"],
                        }
                        for item in self.facts.assumptions
                    ],
                },
            }
        ]
        candidate_rank = {cid: index for index, cid in enumerate(_PA_CANDIDATES)}
        ordered_decisions = sorted(
            view.decisions,
            key=lambda item: (
                candidate_rank.get(item.get("scenario_id"), len(_PA_CANDIDATES)),
                item.get("claim_id") is None,
                str(item.get("claim_id") or ""),
                str(item.get("subject_id") or ""),
                str(item.get("item_kind") or ""),
            ),
        )
        for decision in ordered_decisions:
            steps.append(
                {
                    "label": _decision_label(decision),
                    "kind": "decision",
                    "detail": dict(decision),
                }
            )
        for detail in view.assessments:
            steps.append(
                {
                    "label": f"{_year_label(self.year)} {detail['unit_id']} 计算",
                    "kind": "assessment",
                    "detail": detail,
                }
            )
        for detail in view.scenario_steps:
            steps.append(
                {
                    "label": f"已保存方案：{detail['scenario_id']}",
                    "kind": "scenario",
                    "detail": detail,
                }
            )
        if view.checks:
            reason_codes: list[str] = []
            if view.best is not None:
                reason_codes.append("REQUESTED_SET_DETERMINATE")
            elif view.has_unresolved:
                reason_codes.append("UNKNOWN_COMPETITOR_BLOCKS_BEST")
            else:
                reason_codes.append("NO_ELIGIBLE_CANDIDATE")
            steps.append(
                {
                    "label": "所列候选比较",
                    "kind": "selection",
                    "detail": {
                        "year": self.year,
                        "requested": list(
                            self.facts.y["assessment"]["candidates"]
                        ),
                        "selected": view.selected,
                        "best": view.best,
                        "reason_codes": reason_codes,
                        "candidate_checks": view.checks,
                    },
                }
            )
        steps.append(
            {
                "label": (
                    f"下一年度（{_year_label(self.facts.following)}）"
                    + ("估算" if self.next_amount is not None else "资料不足，未能估算")
                ),
                "kind": "next_year",
                "detail": {
                    "year": self.facts.following,
                    "status": (
                        "available" if self.next_amount is not None else "unavailable"
                    ),
                    "selected": self.next_selected,
                    "amount": (
                        canonical_amount(Fraction(self.next_amount))
                        if self.next_amount is not None
                        else None
                    ),
                    "reason_codes": (
                        []
                        if self.next_amount is not None
                        else [self.next_reason or "TARGET_UNAVAILABLE"]
                    ),
                    "assumptions": [
                        {
                            "assumption_id": item["assumption_id"],
                            "kind": item["kind"],
                            "person_id": item["person_id"],
                            "source_year": item["source_year"],
                            "target_year": item["target_year"],
                        }
                        for item in self.facts.assumptions
                    ],
                },
            }
        )
        return [
            {**step, "step": index} for index, step in enumerate(steps, start=1)
        ]


def _year_label(year: str) -> str:
    return f"{year[:4]}/{year[5:]}"


def _decision_label(decision: Mapping) -> str:
    item_kind = decision.get("item_kind") or "item"
    if decision.get("grant") == "allowed" and decision.get("amount"):
        return f"{item_kind} 免税额 {decision['amount']} 港元"
    if decision.get("entitlement") == "manual":
        return f"{item_kind}：转人工核实"
    return f"{item_kind}：{decision.get('entitlement')}/{decision.get('grant')}"


def _blocked_result(year: str, current: Mapping) -> dict:
    reason_codes = current.get("reason_codes") or []
    reason = "；".join(str(code) for code in reason_codes) or (
        f"本年规则状态不可用（{current.get('status')}）：不产确定税额"
    )
    return {
        "status": "blocked",
        "amounts": {key: None for key in AMOUNT_KEYS},
        "blocked_reason": reason,
        "best_scenario": None,
        "questions": [],
        "steps": [],
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }
