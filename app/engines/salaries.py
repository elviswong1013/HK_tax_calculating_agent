# -*- coding: utf-8 -*-
"""REQ-2/3/4/5/7/9/10 薪俸税引擎 v2（002 P1；Annex 005 事实契约）。

- v2 签名 `calculate_salaries_tax(facts, bundle, *, availability=None)`：
  AvailabilityContext 为显式参数，缺 context 或与 bundle/engine/目标年不匹配
  → E_RULES_STATE_UNVERIFIABLE（无默认放行）。
- 事实模型＝subject 注册表＋逐年事实（§3/§4）；claim ledger 与免税额由共享
  解析器 app.engines.allowances 产出；steps 按 §14 tagged 形状。
- 夫妻：s.29 分支 A（S2）无需联合选择；S3 双方 AI 各基本额；S4 经 §9.1.2
  两遍流程接纳（(a) 成立不得因 (b) 不成立排除；等号不满足）；S5 ineligible＋
  原因；S6 普通婚姻窗口（一次 clean transition 按 s.10(5) 全年合并）；S7 仅
  依赖路径 unresolved＋locator；条件式金额只进非权威 candidate_checks。
- 组件状态：任何 partial → balance=null；complete 且 paid 已知 → final−paid
  （可为负＝估算退税）；blocked 六键全 null；best 仅依候选本年 final＋资格，
  unknown/unresolved 竞争者 → null；显式选中已知候选可保留 final。
- 下一年：缺规则/事实/guard → next=null、partial、missing，本年 final 保留；
  有规则时按原始事实重解析；假设白名单仅 same_income/same_mpf。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Mapping

from app.core.errors import AppError
from app.core.facts_v2 import normalize_v2_facts
from app.engines.allowances import (
    AMOUNT_KEYS,
    Facts,
    analyze_marriage,
    canonical_amount,
    check_availability,
    collect_input_gates,
    dedupe_questions,
    make_question,
    matrix_for,
    mpf_cap,
    node_amount,
    nonseparation_tri,
    reduction_entry,
    resolve_claims,
    resolve_financials,
    salary_unit_tax,
    validate_ledger,
)

_TAX_TYPE = "salaries_tax"
_PROFILE_ID = "part5_shared_v2"


def calculate_salaries_tax(
    facts: Mapping, bundle: Mapping, *, availability: object = None
) -> dict:
    """计算薪俸税（纯函数；availability 显式传入，缺 context 拒算）。

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
    run = _SalaryRun(facts_ctx, ctx)
    return run.execute()


# ---------------------------------------------------------------------------
# 单一年度：候选解析（目标年与下一年共用；下一年不输出 steps）
# ---------------------------------------------------------------------------


class _YearSelection:
    def __init__(
        self,
        bundle: Mapping,
        y: dict,
        subjects: Mapping[str, Any],
        *,
        for_next: bool = False,
    ) -> None:
        self.bundle = bundle
        self.y = y
        self.subjects = subjects
        self.year = y["year"]
        self.for_next = for_next
        self.questions: list[dict] = []
        self.decisions: list[dict] = []
        self.assessments: list[dict] = []
        self.scenario_steps: list[dict] = []
        self.checks: list[dict] = []
        self.scenario_map: dict[str, dict] = {}
        self.finals: dict[str, int] = {}
        self.marriage: dict = {}
        self.best: str | None = None
        self.selected: str | None = None
        self.has_unresolved = False
        self.all_ineligible = False
        self.shared_questions = 0
        self.candidate_question_spans: dict[str, tuple[int, int]] = {}
        self._separate_cache: dict | None = None
        self._separate_emitted = False
        self.input_unresolved = False

    # -- 主流程 --

    def compute(self) -> "_YearSelection":
        y = self.y
        resolve_financials(self.bundle, y, self.subjects, self.questions)
        # E3：活动消费者的 presence/来源/person 事项前置（guard 短路已由入口
        # normalize 先行；此处按候选实际依赖阻断薪俸评税单位）。
        gates = collect_input_gates(y, self.year)
        for question in gates["salary_questions"]:
            self.questions.append(question)
        self.input_unresolved = bool(gates["salary_questions"])
        # E4/§4.10 模式兼容＋s.41(1B)：actual 已选 individual PA 者不得与联合
        # 薪俸评税并存（已证矛盾，不静默按 s10a 接纳）。
        if (
            y["assessment"]["basis"] == "actual"
            and y["assessment"]["salary_joint_elected"] is True
        ):
            for person_id, entry in y["assessment"]["pa_entries"].items():
                if entry["mode"] == "individual":
                    raise AppError(
                        "E_INPUT_CONTRADICTORY",
                        f"已单独选择个人入息课税（{person_id} mode=individual）"
                        "与联合薪俸评税选择并存：模式兼容矛盾（005 §4.10；"
                        "s.41(1B)），不得静默接纳。",
                        field=(
                            f"years.{self.year}.assessment.actual.pa.{person_id}"
                        ),
                    )
        self.matrix = matrix_for(self.bundle, self.year)
        self.mpf_cap = mpf_cap(self.bundle, self.year)
        salaries_data = self.bundle["data"]["salaries"]
        self.bands = salaries_data["progressive_bands"]
        self.standard_bands = salaries_data["standard_rate_bands"]
        self.reduction = reduction_entry(self.bundle, self.year)
        self.marriage = analyze_marriage(y)
        # 005:738：候选内单位序按单位 ID（salary:<pID>）字符串升序，不依赖
        # 请求 participants 数组的原始次序（该数组序仅参与输入校验）。
        self.participants = sorted(
            (entry["person_id"] for entry in y["participants"]),
            key=lambda person_id: f"salary:{person_id}",
        )
        # 主体定位按 role（不按排序后的位置）：单位 ID 序下 participants[0]
        # 不保证是 self；问题/责任人 locator 的 subject 须指向本人（005:462/551）。
        self.self_id = next(
            entry["person_id"]
            for entry in y["participants"]
            if entry["role"] == "self"
        )
        self.couple = len(self.participants) == 2 and y.get("spouse_id") is not None
        self.couple_claimants = set(self.participants) if self.couple else None
        validate_ledger(y, self.couple_claimants)
        self.ai = {
            person_id: self._finance(person_id).get("ai")
            for person_id in self.participants
        }
        # 夫妻法定判定路径：AI 缺失/unknown → needs_input（不自动裁定来源）
        if self.couple and self.marriage["state"] != "unmarried":
            for person_id in self.participants:
                if self.ai[person_id] is None:
                    self.questions.append(
                        make_question(
                            f"years.{self.year}.financials.{person_id}"
                            ".has_assessable_salary_income",
                            self.year,
                            f"请确认 {person_id} 的薪俸税应评税入息存在性"
                            "（has_assessable_salary_income；unknown 不得默认否，"
                            "NAI=0 不得推断）。",
                            subject_id=person_id,
                        )
                    )
        # 候选集合按端点封闭（§9.3）：薪俸端点仅 salary_separate/salary_joint
        for candidate_id in y["assessment"]["candidates"]:
            if candidate_id not in ("salary_separate", "salary_joint"):
                raise AppError(
                    "E_INPUT_EXTRA",
                    f"薪俸税不支持的候选：{candidate_id!r}",
                    field="years.%s.assessment.candidates" % self.year,
                )
        self.shared_questions = len(self.questions)
        # §14/Approved005:738：候选按固定顺序（salary_separate → salary_joint）
        # 评估，不随请求数组次序（trace/checks/scenario 块顺序一致；不隐式
        # 增加未请求候选）。operations 仍记录各单位真实执行顺序。
        requested = set(y["assessment"]["candidates"])
        for candidate_id in ("salary_separate", "salary_joint"):
            if candidate_id not in requested:
                continue
            question_start = len(self.questions)
            if candidate_id == "salary_separate":
                self._eval_separate()
            else:
                self._eval_joint()
            self.candidate_question_spans[candidate_id] = (
                question_start,
                len(self.questions),
            )
        self._select()
        return self

    # -- 选择与最佳 --

    def _select(self) -> None:
        eligible = [check for check in self.checks if check["admission"] == "eligible"]
        self.has_unresolved = any(
            check["admission"] == "unresolved" for check in self.checks
        )
        self.all_ineligible = bool(self.checks) and not eligible and not self.has_unresolved
        order = {"salary_separate": 0, "salary_joint": 1}
        if self.has_unresolved:
            self.best = None
        elif eligible:
            self.best = min(
                eligible,
                key=lambda check: (
                    self.amount_of(check["candidate_id"]) or 0,
                    order.get(check["candidate_id"], 9),
                ),
            )["candidate_id"]
        else:
            self.best = None
        selection = self.y["assessment"]["selection"]
        self.selection_requested = selection
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

    def _marriage_question(self, reason: str | None = None) -> None:
        text = (
            "请确认本课税年度的婚姻期间与配偶事实（婚姻缺失/未知不得默认 single）；"
            "涉及分居、复合、换配偶或期间缺口时仅依赖路径转人工/未决。"
        )
        if reason:
            text += f"（原因标签：{reason}）"
        self.questions.append(
            make_question(f"years.{self.year}.household.marriage", self.year, text)
        )

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

    def _unit_detail(
        self,
        scenario_id: str,
        unit_id: str,
        branch: str,
        actors: list[str],
        unit: dict,
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
            "shares": [],
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

    def _allowance_decision(
        self,
        scenario_id: str,
        item_kind: str,
        claimant_id: str,
        subject_id: str,
        amount: int,
        reason_codes: list[str],
        rule_refs: list[str],
    ) -> dict:
        """基础额（basic/married）decision（§14 示例形状：claim_id=null）。"""
        return {
            "year": self.year,
            "scenario_id": scenario_id,
            "claim_id": None,
            "claimant_id": claimant_id,
            "subject_id": subject_id,
            "item_kind": item_kind,
            "entitlement": "eligible",
            "grant": "allowed",
            "amount": str(amount),
            "reason_codes": sorted(set(reason_codes)),
            "rule_refs": sorted(set(rule_refs)),
        }

    def _deduction_decisions(self, scenario_id: str) -> list[dict]:
        """Part 4A deduction decision 逐条入 steps（requested≠allowed；§5.1/§14）。

        C4/R-E56：allowed="0" 且无未决原因码＝已解析零额（cap 用尽等），不得
        与「资格未知」混同；requested＋RAW_FACTS_NOT_MET 须记 ineligible
        （与 PA/既有语义一致，不得映射为 unresolved）。
        """
        rows: list[dict] = []
        for person_id in self.participants:
            finance = self._finance(person_id)
            for row in finance.get("deduction_decisions", []):
                allowed = row.get("allowed")
                requested = row.get("requested")
                reasons = set(row.get("reason_codes") or [])
                if allowed not in (None, "0"):
                    entitlement, grant = "eligible", "allowed"
                elif requested is False:
                    entitlement, grant = "ineligible", "not_requested"
                elif requested is True and "RAW_FACTS_NOT_MET" in reasons:
                    entitlement, grant = "ineligible", "ineligible"
                elif requested is True and "RAW_FACTS_UNKNOWN" not in reasons:
                    # 已解析的零额（如 cap 用尽）：资格成立、金额 0。
                    entitlement, grant = "eligible", "allowed"
                else:
                    entitlement, grant = "unknown", "unresolved"
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
                        "amount": allowed if grant == "allowed" else None,
                        "reason_codes": sorted(set(row.get("reason_codes") or [])),
                        "rule_refs": [],
                    }
                )
        return rows

    # -- S2/S3/单身的分别评税配置 --

    def _separate_allocation(self) -> dict:
        if not self.couple:
            return {"recipient": None, "unresolved": None}
        state = self.marriage["state"]
        if state == "unknown":
            return {"recipient": None, "unresolved": "MARRIAGE_UNKNOWN"}
        if state == "unmarried":
            return {"recipient": None, "unresolved": None}
        ai_true = [pid for pid in self.participants if self.ai[pid] is True]
        ai_false = [pid for pid in self.participants if self.ai[pid] is False]
        if any(self.ai[pid] is None for pid in self.participants):
            return {"recipient": None, "unresolved": "AI_UNKNOWN"}
        if len(ai_true) == 2:
            return {"recipient": None, "unresolved": None}
        if len(ai_false) == 1 and len(ai_true) == 1:
            if state in ("sequential", "s7"):
                # E4：分支 A 依赖 s.29 婚姻归属；S7 顺序配偶/分居变化下不得
                # 默认分支 A（独立 S3/单身路径不受影响）。
                self.questions.append(
                    make_question(
                        f"years.{self.year}.household.marriage",
                        self.year,
                        "同一课税年度内的顺序配偶/分居变化（S7）：s.29 分支 A "
                        "归属决定未决，不得默认授予；请人工确认婚姻期间与配偶事实。"
                        f"（原因标签：{self.marriage['reason'] or 'S7'}）",
                        subject_id=self.self_id,
                    )
                )
                return {
                    "recipient": None,
                    "unresolved": self.marriage["reason"] or "S7",
                }
            false_pid = ai_false[0]
            true_pid = ai_true[0]
            entry = self.y["assessment"]["pa_entries"].get(false_pid)
            if entry is None or entry["mode"] == "unknown":
                self.questions.append(
                    make_question(
                        f"years.{self.year}.assessment.actual.pa.{false_pid}",
                        self.year,
                        f"请确认配偶 {false_pid} 的实际个人入息课税（PA）评税选择"
                        "（mode：none/individual/joint）——s.29 分支 A 判定需要该事实，"
                        "缺键不等于 none。",
                        subject_id=false_pid,
                    )
                )
                return {"recipient": None, "unresolved": "SPOUSE_PA_MODE_UNKNOWN"}
            if entry["mode"] == "none":
                return {"recipient": true_pid, "unresolved": None}
            return {"recipient": None, "unresolved": None}
        return {"recipient": None, "unresolved": None}

    # -- 分别评税（含 A/B 共同前置；下一年与检查复用） --

    def _compute_separate(self) -> dict:
        if self._separate_cache is not None:
            return self._separate_cache
        allocation = self._separate_allocation()
        if allocation["unresolved"]:
            result = {
                "unresolved": allocation["unresolved"],
                "units": [],
                "totals": None,
                "holder_amounts": {},
                "decisions": [],
            }
            self._separate_cache = result
            return result
        nomination = self._child_nomination()
        resolution = resolve_claims(
            y=self.y,
            subjects=self.subjects,
            matrix=self.matrix,
            year=self.year,
            window=self.y["window"],
            scenario_id="salary_separate",
            branch_a_recipient=allocation["recipient"],
            spouse_dda_holder=allocation["recipient"],
            child_nominee=nomination["holder"],
            child_nomination_unknown=nomination["unknown"],
            questions=self.questions,
        )
        self._parent_mutex_questions(resolution["parent_grants"])
        allowances: dict[str, int] = {pid: 0 for pid in self.participants}
        base_decisions: list[dict] = []
        if not self.couple:
            single_id = self.self_id
            allowances[single_id] = node_amount(self.matrix, "basic", self.year)
            base_decisions.append(
                self._allowance_decision(
                    "salary_separate", "basic", single_id, single_id,
                    allowances[single_id], ["BASIC_ENTITLED"], ["s28"],
                )
            )
        elif allocation["recipient"] is not None:
            recipient = allocation["recipient"]
            allowances[recipient] = node_amount(self.matrix, "married", self.year)
            base_decisions.append(
                self._allowance_decision(
                    "salary_separate", "married", recipient, recipient,
                    allowances[recipient], ["MARRIED_ENTITLED"], ["s29"],
                )
            )
        else:
            basic = node_amount(self.matrix, "basic", self.year)
            for pid in self.participants:
                allowances[pid] = basic
                base_decisions.append(
                    self._allowance_decision(
                        "salary_separate", "basic", pid, pid, basic,
                        ["BASIC_ENTITLED"], ["s28"],
                    )
                )
        for holder, amount in resolution["holder_amounts"].items():
            allowances[holder] = allowances.get(holder, 0) + amount
        units: list[dict] = []
        if not self.input_unresolved:
            for pid in self.participants:
                finance = self._finance(pid)
                unit = salary_unit_tax(
                    branch="salary_floor_min_v1",
                    nai=finance.get("nai", Fraction(0)),
                    part4a=min(finance.get("mpf", Fraction(0)), Fraction(self.mpf_cap))
                    + finance.get("deductions", Fraction(0)),
                    allowances=allowances.get(pid, 0),
                    bands=self.bands,
                    standard_bands=self.standard_bands,
                    reduction=self.reduction,
                )
                units.append({"person_id": pid, "unit": unit})
        totals = (
            {
                "before": sum(item["unit"]["before"] for item in units),
                "reduction": sum(item["unit"]["reduction"] for item in units),
                "final": sum(item["unit"]["final"] for item in units),
            }
            if units
            else None
        )
        if self.input_unresolved:
            unresolved_reason = "INPUT_UNRESOLVED"
        elif resolution["unresolved"]:
            unresolved_reason = "CLAIM_UNRESOLVED"
        else:
            unresolved_reason = None
        result = {
            "unresolved": unresolved_reason,
            "units": units,
            "totals": totals,
            "holder_amounts": resolution["holder_amounts"],
            "decisions": base_decisions
            + resolution["decisions"]
            + self._deduction_decisions("salary_separate"),
            "person_nai": {
                pid: self._finance(pid).get("nai", Fraction(0)) for pid in self.participants
            },
            "person_part4a": {
                pid: min(
                    self._finance(pid).get("mpf", Fraction(0)), Fraction(self.mpf_cap)
                )
                + self._finance(pid).get("deductions", Fraction(0))
                for pid in self.participants
            },
            "person_allowances": allowances,
        }
        self._separate_cache = result
        return result

    def _emit_separate(self, result: dict, scenario_id: str = "salary_separate") -> None:
        if self._separate_emitted or not result["units"]:
            return
        for item in result["units"]:
            self.assessments.append(
                self._unit_detail(
                    scenario_id,
                    f"salary:{item['person_id']}",
                    "salary_floor_min_v1",
                    [item["person_id"]],
                    item["unit"],
                )
            )
        self.scenario_steps.append(
            self._scenario_step(
                scenario_id,
                [f"salary:{item['person_id']}" for item in result["units"]],
                result["totals"],
            )
        )
        self.scenario_map[scenario_id] = result["totals"]
        self.finals[scenario_id] = result["totals"]["final"]
        self._separate_emitted = True

    # -- 候选：salary_separate --

    def _eval_separate(self) -> dict:
        check = {
            "candidate_id": "salary_separate",
            "admission": None,
            "legal_path": None,
            "responsible_person_id": None,
            "separate_final": None,
            "conditional_final": None,
            "reason_codes": [],
        }
        if self.marriage["state"] == "unknown":
            check["admission"] = "unresolved"
            check["reason_codes"] = ["MARRIAGE_UNKNOWN"]
            self._marriage_question("MARRIAGE_UNKNOWN")
            self.checks.append(check)
            return check
        # F4/R4（005 §4.10 依赖表第 5 行）：ordinary 夫妻 actual 分别评税须
        # 先核对实际 salary 模式（salary_joint_elected）。缺失/unknown→该候选
        # 未决并定位；已知联合选择→与 separate 请求冲突，按既有 P5 冲突规则
        # 不接纳（不静默改判 joint）。单身/hypothetical/S7 窄边界不在此消费。
        if (
            self.couple
            and self.marriage["state"] == "ordinary"
            and self.y["assessment"]["basis"] == "actual"
        ):
            elected = self.y["assessment"]["salary_joint_elected"]
            if elected is not False:
                # 先执行既有结构对账（如父母额与 s.26D 跨人互斥）：与 salary
                # 模式无关的已证矛盾不得被本 gate 的未决返回遮蔽；计算结果不
                # 被接纳（不 emit、不扩展 decisions）。
                self._compute_separate()
                check["admission"] = "unresolved"
                if elected is True:
                    check["reason_codes"] = ["ACTUAL_SALARY_MODE_CONFLICT"]
                else:
                    check["reason_codes"] = [
                        "ACTUAL_SALARY_JOINT_ELECTED_REQUIRED"
                    ]
                    self.questions.append(
                        make_question(
                            f"years.{self.year}.assessment.actual."
                            "salary_joint_elected",
                            self.year,
                            "actual 普通婚姻夫妻请求单独薪俸评税：请确认实际未选择"
                            "联合评税（salary_joint_elected；缺失/unknown 不等于"
                            "未选）。",
                        )
                    )
                self.checks.append(check)
                return check
        result = self._compute_separate()
        self.decisions.extend(result["decisions"])
        if result["unresolved"]:
            check["admission"] = "unresolved"
            check["reason_codes"] = [result["unresolved"]]
            self.checks.append(check)
            return check
        check["admission"] = "eligible"
        check["reason_codes"] = ["SEPARATE_DEFAULT"]
        if not self.couple:
            check["responsible_person_id"] = self.self_id
        check["separate_final"] = canonical_amount(Fraction(result["totals"]["final"]))
        self._emit_separate(result)
        self.checks.append(check)
        return check

    # -- 候选：salary_joint（§9.1.2 两遍流程） --

    def _joint_nomination(self) -> dict | None:
        assessment = self.y["assessment"]
        if assessment["basis"] == "hypothetical":
            return assessment["hypothetical_nomination"]
        return assessment["actual_nomination"]

    def _spouse_dda_depends(self) -> bool:
        spouse_id = self.y.get("spouse_id")
        return any(
            claim["kind"] == "disabled_dependant" and claim["subject_id"] == spouse_id
            for claim in self.y["claims"]
        )

    def _joint_conditional(self, responsible: str | None, *, commit: bool) -> dict:
        nomination = self._child_nomination()
        resolution = resolve_claims(
            y=self.y,
            subjects=self.subjects,
            matrix=self.matrix,
            year=self.year,
            window=self.y["window"],
            scenario_id="salary_joint",
            branch_a_recipient=None,
            spouse_dda_holder=responsible,
            child_nominee=nomination["holder"],
            child_nomination_unknown=nomination["unknown"],
            questions=self.questions,
        )
        allowances = node_amount(self.matrix, "married", self.year) + sum(
            resolution["holder_amounts"].values()
        )
        nai_total = sum(
            (self._finance(pid).get("nai", Fraction(0)) for pid in self.participants),
            Fraction(0),
        )
        part4a_total = sum(
            (
                min(self._finance(pid).get("mpf", Fraction(0)), Fraction(self.mpf_cap))
                + self._finance(pid).get("deductions", Fraction(0))
                for pid in self.participants
            ),
            Fraction(0),
        )
        unit = salary_unit_tax(
            branch="salary_floor_min_v1",
            nai=nai_total,
            part4a=part4a_total,
            allowances=allowances,
            bands=self.bands,
            standard_bands=self.standard_bands,
            reduction=self.reduction,
        )
        if commit:
            self._parent_mutex_questions(resolution["parent_grants"])
            married_holder = responsible if responsible is not None else self.self_id
            self.decisions.append(
                self._allowance_decision(
                    "salary_joint",
                    "married",
                    married_holder,
                    married_holder,
                    node_amount(self.matrix, "married", self.year),
                    ["MARRIED_ENTITLED"],
                    ["s29"],
                )
            )
            self.decisions.extend(resolution["decisions"])
            self.decisions.extend(self._deduction_decisions("salary_joint"))
            self.assessments.append(
                self._unit_detail(
                    "salary_joint",
                    "salary:joint",
                    "salary_floor_min_v1",
                    list(self.participants),
                    unit,
                )
            )
            totals = {
                "before": unit["before"],
                "reduction": unit["reduction"],
                "final": unit["final"],
            }
            self.scenario_steps.append(
                self._scenario_step("salary_joint", ["salary:joint"], totals)
            )
            self.scenario_map["salary_joint"] = totals
            self.finals["salary_joint"] = unit["final"]
        return {"unit": unit, "resolution": resolution}

    def _eval_joint(self) -> dict:
        check = {
            "candidate_id": "salary_joint",
            "admission": None,
            "legal_path": None,
            "responsible_person_id": None,
            "separate_final": None,
            "conditional_final": None,
            "reason_codes": [],
        }
        state = self.marriage["state"]
        if state == "unknown":
            check["admission"] = "unresolved"
            check["reason_codes"] = ["MARRIAGE_UNKNOWN"]
            self._marriage_question("MARRIAGE_UNKNOWN")
            self.checks.append(check)
            return check
        if state == "unmarried":
            check["admission"] = "ineligible"
            check["reason_codes"] = ["NOT_MARRIED"]
            self.checks.append(check)
            return check
        if any(self.ai[pid] is None for pid in self.participants):
            check["admission"] = "unresolved"
            check["reason_codes"] = ["AI_UNKNOWN"]
            self.checks.append(check)
            return check
        if not all(self.ai[pid] is True for pid in self.participants):
            check["admission"] = "ineligible"
            check["reason_codes"] = ["JOINT_REQUIRES_BOTH_AI"]
            self.checks.append(check)
            return check
        assessment = self.y["assessment"]
        # E4/CR-E：election 事项（本年度参与者）只阻依赖候选（joint）；独立
        # separate 保留。§14 admission 枚举无 manual → unresolved。
        participant_ids = set(self.participants)
        for issue in self.y.get("manual_issues") or []:
            if issue["scope"] == "election" and issue["id"] in participant_ids:
                check["admission"] = "unresolved"
                check["reason_codes"] = ["ELECTION_ISSUE_UNRESOLVED"]
                self.checks.append(check)
                return check
        if assessment["basis"] == "actual":
            # actual 请求 joint：salary_joint_elected 缺失/unknown/未选均不得默认接纳
            if assessment["salary_joint_elected"] is not True:
                check["admission"] = "unresolved"
                check["reason_codes"] = ["ACTUAL_JOINT_ELECTION_REQUIRED"]
                self.questions.append(
                    make_question(
                        f"years.{self.year}.assessment.actual.salary_joint_elected",
                        self.year,
                        "actual 基线下请求联合评税：请确认已实际选择联合薪俸评税"
                        "（salary_joint_elected；缺失/unknown/未选不得默认接纳）。",
                    )
                )
                self.checks.append(check)
                return check
            for pid in self.participants:
                pa_entry = assessment["pa_entries"].get(pid)
                if pa_entry is None or pa_entry["mode"] == "unknown":
                    check["admission"] = "unresolved"
                    check["reason_codes"] = ["ACTUAL_PA_MODE_UNRESOLVED"]
                    self.questions.append(
                        make_question(
                            f"years.{self.year}.assessment.actual.pa.{pid}",
                            self.year,
                            "actual 联合薪俸评税需要当年 PA 模式事实：缺键/unknown"
                            "不等于 none，请补全后重算。",
                            subject_id=pid,
                        )
                    )
                    self.checks.append(check)
                    return check
            history = assessment.get("history")
            if history != "ordinary":
                check["admission"] = "unresolved"
                check["reason_codes"] = ["ACTUAL_JOINT_HISTORY_NOT_ORDINARY"]
                if history is None or history == "unknown":
                    self.questions.append(
                        make_question(
                            f"years.{self.year}.assessment.actual.history",
                            self.year,
                            "actual 联合评税的选择历史（history）缺失/unknown："
                            "请确认 ordinary/withdrawn/disputed 后重算。",
                        )
                    )
                self.checks.append(check)
                return check
        if state in ("sequential", "s7"):
            check["admission"] = "unresolved"
            check["reason_codes"] = [self.marriage["reason"]]
            self.questions.append(
                make_question(
                    f"years.{self.year}.household.marriage",
                    self.year,
                    "同一课税年度内的顺序配偶/分居变化（S7）：s.29/婚姻归属决定"
                    "未决，不得默认授予；请人工确认婚姻期间与配偶事实。"
                    f"（原因标签：{self.marriage['reason'] or 'S7'}）",
                    subject_id=self.self_id,
                )
            )
            self.checks.append(check)
            return check

        separate = self._compute_separate()
        if separate["unresolved"]:
            check["admission"] = "unresolved"
            check["reason_codes"] = [separate["unresolved"]]
            self.checks.append(check)
            return check
        check["separate_final"] = canonical_amount(Fraction(separate["totals"]["final"]))
        a_condition = any(
            separate["person_part4a"][pid] + Fraction(separate["person_allowances"][pid])
            > separate["person_nai"][pid]
            for pid in self.participants
        )
        b_premise = all(
            separate["person_nai"][pid]
            - separate["person_part4a"][pid]
            - Fraction(separate["person_allowances"][pid])
            > 0
            for pid in self.participants
        )
        if a_condition:
            chargeable = [
                item["person_id"]
                for item in separate["units"]
                if item["unit"]["final"] > 0
            ]
            if len(chargeable) != 1:
                check["admission"] = "unresolved"
                check["reason_codes"] = ["S10A_RESPONSIBLE_AMBIGUOUS"]
                self.checks.append(check)
                return check
            responsible = chargeable[0]
            nomination = self._joint_nomination()
            if (
                nomination is not None
                and nomination["status"] == "named"
                and nomination["person_id"] != responsible
            ):
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    "(a) 路径派生责任人"
                    f"（{responsible}）与 salary_joint_nomination 提名"
                    f"（{nomination['person_id']}）冲突：不得静默按任何一方接纳",
                    field=f"years.{self.year}.assessment.actual.salary_joint_nomination",
                )
            if nomination is not None and nomination["status"] == "disputed":
                check["admission"] = "unresolved"
                check["reason_codes"] = ["JOINT_NOMINATION_DISPUTED"]
                self.checks.append(check)
                return check
            formal = self._joint_conditional(responsible, commit=True)
            check.update(
                admission="eligible",
                legal_path="s10a",
                responsible_person_id=responsible,
                conditional_final=canonical_amount(Fraction(formal["unit"]["final"])),
                reason_codes=["S10A_RESPONSIBLE_DERIVED"],
            )
            self.checks.append(check)
            return check
        if not b_premise:
            check["admission"] = "ineligible"
            check["reason_codes"] = ["S5_NO_LEGAL_BRANCH"]
            self.checks.append(check)
            return check
        nomination = self._joint_nomination()
        nominal_field = (
            "actual" if assessment["basis"] == "actual" else "hypothetical"
        ) + "_salary_joint_nomination"
        depends = self._spouse_dda_depends()
        if nomination is not None and nomination["status"] == "named":
            responsible = nomination["person_id"]
        elif nomination is not None and nomination["status"] == "disputed":
            check["admission"] = "unresolved"
            check["reason_codes"] = ["JOINT_NOMINATION_DISPUTED"]
            self.checks.append(check)
            return check
        elif nomination is not None and nomination["status"] in ("unknown", "none"):
            # 显式 unknown/none：s.10(2)(b) 归属不可确定 → 候选未决＋定位追问
            check["admission"] = "unresolved"
            check["reason_codes"] = ["JOINT_NOMINATION_REQUIRED"]
            nomination_field = (
                "actual" if assessment["basis"] == "actual" else "hypothetical"
            ) + "_salary_joint_nomination"
            self.questions.append(
                make_question(
                    f"years.{self.year}.assessment.{nomination_field}",
                    self.year,
                    "s.10(2)(b) 归属需明确 salary nomination（unknown/none 不得默认 "
                    "self 或收入较高者）：请指定提名人。",
                )
            )
            self.checks.append(check)
            return check
        elif depends:
            check["admission"] = "unresolved"
            check["reason_codes"] = ["JOINT_NOMINATION_REQUIRED"]
            if nomination is None:
                self.questions.append(
                    make_question(
                        f"years.{self.year}.assessment.{nominal_field}",
                        self.year,
                        "s.10(2)(b) 归属需明确 salary nomination：提名字段缺失（≠none），"
                        "请补全后重算（不得从 child nominee/收入较高者推断）。",
                    )
                )
            self.checks.append(check)
            return check
        else:
            responsible = None
        conditional = self._joint_conditional(responsible, commit=False)
        conditional_final = conditional["unit"]["final"]
        check["conditional_final"] = canonical_amount(Fraction(conditional_final))
        if responsible is None:
            if separate["totals"]["final"] > conditional_final:
                check["admission"] = "unresolved"
                check["reason_codes"] = ["JOINT_RESPONSIBLE_REQUIRED"]
                if nomination is None:
                    self.questions.append(
                        make_question(
                            f"years.{self.year}.assessment.{nominal_field}",
                            self.year,
                            "s.10(2)(b) 归属需明确 salary nomination：提名字段缺失"
                            "（≠none），请补全后重算（不得从 child nominee/收入较高者"
                            "推断）。",
                        )
                    )
            else:
                check["admission"] = "ineligible"
                check["reason_codes"] = ["S5_SEPARATE_NOT_EXCEEDED"]
            self.checks.append(check)
            return check
        check["responsible_person_id"] = responsible
        if separate["totals"]["final"] <= conditional_final:
            check["admission"] = "ineligible"
            check["reason_codes"] = ["S5_SEPARATE_NOT_EXCEEDED"]
            self.checks.append(check)
            return check
        formal = self._joint_conditional(responsible, commit=True)
        check.update(
            admission="eligible",
            legal_path="s10b",
            responsible_person_id=responsible,
            conditional_final=canonical_amount(Fraction(formal["unit"]["final"])),
            reason_codes=["S10B_NOMINATION_ACCEPTED"],
        )
        self.checks.append(check)
        return check


# ---------------------------------------------------------------------------
# 目标年运行（信封、下一年、steps）
# ---------------------------------------------------------------------------


class _SalaryRun:
    def __init__(self, facts: Facts, availability: Mapping) -> None:
        self.facts = facts
        self.availability = availability
        self.year = facts.year
        self.next_amount: int | None = None
        self.next_selected: str | None = None
        self.next_reason: str | None = None

    def execute(self) -> dict:
        selection = _YearSelection(
            self.facts.bundle, self.facts.y, self.facts.subjects
        ).compute()
        self.selection = selection
        self._compute_next()
        questions = dedupe_questions(selection.questions)
        checks = selection.checks
        eligible = [
            check for check in checks if check["admission"] == "eligible"
        ]
        selected_final = selection.selected_final()
        paid = self.facts.y["provisional_paid"]
        next_amount = self.next_amount
        # 候选级问题与结果级状态分离（R7.1）：仅 selected 自身（含共享输入）
        # 的未决问题才升级为 needs_input；竞争候选问题不清空已知 selected。
        if selection.selected is not None:
            span = selection.candidate_question_spans.get(selection.selected)
            own_questions = selection.shared_questions + (
                (span[1] - span[0]) if span is not None else 0
            )
        else:
            own_questions = len(questions)
        if selected_final is not None:
            if own_questions > 0:
                status = "needs_input"
            else:
                status = "complete" if (paid is not None and next_amount is not None) else "partial"
        elif selection.selected is not None:
            chosen = next(
                (
                    check
                    for check in checks
                    if check["candidate_id"] == selection.selected
                ),
                None,
            )
            if chosen is not None and chosen["admission"] == "unresolved":
                status = "needs_input"
            elif selection.all_ineligible:
                status = "blocked"
            else:
                status = "partial"
        elif selection.all_ineligible:
            status = "blocked"
        elif eligible:
            # selection=best 未定但存在已知可算 scenario → partial（top-level null）
            status = "partial"
        else:
            status = "needs_input"
        if status == "needs_input" and not questions:
            # 保证定位项存在（no-eligible 状态必有可定位原因）
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
        amounts = self._amounts(status, selection, selected_final, paid)
        missing = self._missing_components(paid)
        self._complete_scenario_trace(selection, status, selected_final, paid)
        steps = self._steps(selection, status)
        result = {
            "status": status,
            "amounts": amounts,
            "best_scenario": selection.best,
            "questions": questions,
            "steps": steps,
            "warnings": [],
            "unsupported": [],
            "pending_verification": [],
            "missing_components": missing,
        }
        if status == "blocked":
            result["blocked_reason"] = "所列候选全部明确不合资格（S5）：不产确定税额"
        return result

    def _complete_scenario_trace(
        self,
        selection: _YearSelection,
        status: str,
        selected_final: int | None,
        paid: Fraction | None,
    ) -> None:
        """组件确定后完成 trace：selected scenario 六键完整（§10/§14）。"""
        if selection.selected is None:
            return
        for detail in selection.scenario_steps:
            if detail.get("scenario_id") != selection.selected:
                continue
            detail["status"] = status
            detail["amounts"]["next_provisional"] = (
                canonical_amount(Fraction(self.next_amount))
                if self.next_amount is not None
                else None
            )
            if status == "complete" and paid is not None and selected_final is not None:
                detail["amounts"]["balance"] = canonical_amount(
                    Fraction(selected_final) - paid
                )

    # -- 金额信封 --

    def _amounts(
        self,
        status: str,
        selection: _YearSelection,
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
            selection.scenario_map.get(selection.selected)
            if selection.selected is not None
            else None
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
        balance = None
        if status == "complete" and paid is not None and selected_final is not None:
            balance = canonical_amount(Fraction(selected_final) - paid)
        return {
            "before_reduction": str(scenario["before"]),
            "reduction": str(scenario["reduction"]),
            "final_after_reduction": str(scenario["final"]),
            "provisional_paid": paid_text,
            "next_provisional": next_text,
            "balance": balance,
        }

    def _missing_components(self, paid: Fraction | None) -> list[str]:
        missing: list[str] = []
        if paid is None:
            missing.append("provisional_paid")
        if self.next_amount is None:
            missing.append(f"next_provisional:{self.facts.following}")
        manual = [
            decision["claim_id"]
            for decision in self.selection.decisions
            if decision.get("entitlement") == "manual"
        ]
        for claim_id in sorted(set(manual)):
            missing.append(f"manual:{claim_id}")
        return missing

    # -- 下一年 --

    def _compute_next(self) -> None:
        try:
            next_y = self.facts.build_next()
        except AppError as exc:
            if exc.code == "E_RULES_STATE_UNVERIFIABLE":
                # E7：下一年活动规则不可核验 → 仅抑制 next，本年结果保留。
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
            selection = _YearSelection(
                self.facts.bundle, next_y, self.facts.subjects, for_next=True
            ).compute()
        except AppError as exc:
            if exc.code == "E_RULES_STATE_UNVERIFIABLE":
                # E7：next 活动规则缺键/畸形 → 年度隔离（仅抑 next），
                # E_INPUT_*／E_DEDUCTION_UNSUPPORTED 等输入拒绝照旧重抛。
                self.next_reason = exc.code
                return
            raise
        if selection.questions or selection.all_ineligible:
            self.next_reason = "TARGET_FACTS_INCOMPLETE"
            return
        final = selection.selected_final()
        if final is None:
            self.next_reason = "TARGET_UNRESOLVED"
            return
        self.next_amount = final
        self.next_selected = selection.selected

    # -- steps --

    def _steps(self, selection: _YearSelection, status: str) -> list[dict]:
        steps: list[dict] = [
            {
                "label": (
                    f"{_year_label(self.year)} 薪俸税（税率与取整口径：累进／标准"
                    "两径分别按税率带计算并 floor 取整，宽减 floor 后扣）"
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
        # 005:738：年度→固定候选→单位。逐候选（salary_separate→salary_joint）
        # 连续输出其 decision/assessment/scenario 块；候选内单位保持固定序。
        # 只重组 tagged steps，不改择优/平局/operations 的实际执行顺序。
        by_candidate: dict[str, dict[str, list[dict]]] = {
            candidate_id: {"decision": [], "assessment": [], "scenario": []}
            for candidate_id in ("salary_separate", "salary_joint")
        }
        for kind, rows in (
            ("decision", selection.decisions),
            ("assessment", selection.assessments),
            ("scenario", selection.scenario_steps),
        ):
            for detail in rows:
                by_candidate[detail["scenario_id"]][kind].append(detail)
        for candidate_id in ("salary_separate", "salary_joint"):
            block = by_candidate[candidate_id]
            for decision in block["decision"]:
                steps.append(
                    {
                        "label": _decision_label(decision),
                        "kind": "decision",
                        "detail": dict(decision),
                    }
                )
            for detail in block["assessment"]:
                steps.append(
                    {
                        "label": f"{_year_label(self.year)} {detail['unit_id']} 计算",
                        "kind": "assessment",
                        "detail": detail,
                    }
                )
            for detail in block["scenario"]:
                steps.append(
                    {
                        "label": f"已保存方案：{detail['scenario_id']}",
                        "kind": "scenario",
                        "detail": detail,
                    }
                )
        if selection.checks:
            reason_codes: list[str] = []
            if selection.best is not None:
                reason_codes.append("REQUESTED_SET_DETERMINATE")
            elif selection.has_unresolved:
                reason_codes.append("UNKNOWN_COMPETITOR_BLOCKS_BEST")
            else:
                reason_codes.append("NO_ELIGIBLE_CANDIDATE")
            steps.append(
                {
                    "label": (
                        "所列候选比较（条件式比较，非已接纳：条件式金额仅见 "
                        "candidate_checks，非已接纳税额）"
                    ),
                    "kind": "selection",
                    "detail": {
                        "year": self.year,
                        "requested": list(
                            self.facts.y["assessment"]["candidates"]
                        ),
                        "selected": selection.selected,
                        "best": selection.best,
                        "reason_codes": reason_codes,
                        "candidate_checks": selection.checks,
                    },
                }
            )
        next_detail = self._next_detail()
        steps.append(
            {
                "label": (
                    f"下一年度（{_year_label(self.facts.following)}）"
                    + ("估算" if self.next_amount is not None else "资料不足，未能估算")
                ),
                "kind": "next_year",
                "detail": next_detail,
            }
        )
        return [
            {**step, "step": index} for index, step in enumerate(steps, start=1)
        ]

    def _next_detail(self) -> dict:
        reason_codes: list[str] = []
        if self.next_amount is not None:
            status = "available"
        else:
            status = "unavailable"
            reason_codes = [self.next_reason or "TARGET_UNAVAILABLE"]
        return {
            "year": self.facts.following,
            "status": status,
            "selected": self.next_selected,
            "amount": (
                canonical_amount(Fraction(self.next_amount))
                if self.next_amount is not None
                else None
            ),
            "reason_codes": sorted(reason_codes),
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
        }


# ---------------------------------------------------------------------------
# 信封辅助
# ---------------------------------------------------------------------------


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
        "steps": [
            {
                "step": 1,
                "label": f"{_year_label(year)} 薪俸税",
                "kind": "context",
                "detail": {
                    "year": year,
                    "tax_type": _TAX_TYPE,
                    "profile_id": _PROFILE_ID,
                    "assumptions": [],
                },
            }
        ],
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }
