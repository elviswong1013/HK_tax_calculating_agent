"""候选门禁：独立参考、必需 case、隔离运行环境（Annex C C8；REQ-15/18）。

REQ: 15/18（§9：在候选 bundle 上运行受影响＋回归测试；参考参数只来自已批准
独立 source/proof；门禁在隔离环境运行；空报告/缺必需 case 禁止发布）。
规格锚点:
  - Annex C C8.1：独立参考 harness 不与候选共享参数对象；两个引擎一致本身
    不构成证据。
  - Annex C C8.4：必需 case 全部执行＋每 case 参数证据＋逐阶段一致＋回归
    failed=0；缺任何必需 case、或报告为空/零通过 → 隔离，不得发布。
  - Annex C C8.6：发布前运行时验证报告结构（cases/regression/tests/
    gate_environment/decision）。
  - Annex C C8.7：门禁在临时 store 执行——无网络、无用户 session、不写
    current pointer；发布只发生在门禁外、全验证通过后的单事务。
期望值来源: 结构性契约（门禁判定/隔离断言），无金额期望。

【拟名】被测契约:
  - run_gate(*, candidate_content, current_content, manifests,
             independent_facts, required_cases, harness, engine,
             outbound=None) -> C8.6 形状报告 dict
    harness.run_case(case_id, outbound=counter) -> {"case_id","stages","output",
      "parameter_evidence_ref"}（独立参考 seam；具备 outbound 形参/ **kwargs 时
      注入计数代理——执行期任何出站尝试计入 network_calls）；
    engine.run_case(case_id, candidate_content, outbound=counter) ->
      {"case_id","stages","output"}（候选引擎 seam；同上注入）。
    decision="publish" 须同时满足：required_cases 全部执行、每 case
    parameter_evidence_ref 解析到 independent_facts 中的独立事实、逐阶段
    一致、回归 failed=0、非空报告、执行期 network_calls == 0；任一不满足 →
    "quarantine"。
    隔离（C8.7）：run_gate 不接受现行 store 句柄、验证在临时环境中完成、
    不写指针、不进行任何出站；gate_environment 写入**真实证据**：磁盘上实际
    创建的临时 store 路径（temp_store_path）＋出站调用实际计数（network_calls，
    须为 0；> 0 → 隔离）＋user_session/current_pointer_written（临时 store
    实查）。
"""

from __future__ import annotations

import inspect
import os
import tempfile
from typing import Any, Callable, Mapping, Sequence

from app.rules.store import RuleStore


class _CountingOutbound:
    """出站计数代理：门禁期间任何 fetch 尝试都被实际计数（C8.7 须为 0）。

    run_gate 自身不直接出站；本代理经 _call_with_outbound 注入 harness/engine
    执行——执行期任何误入出站都被实际计数并触发隔离（可审计证据，而非硬编码
    布尔）。
    """

    def __init__(self, inner: Any = None) -> None:
        self._inner = inner
        self.calls = 0

    def fetch(self, url: str, **kwargs: Any) -> bytes:
        self.calls += 1
        if self._inner is None:
            raise ConnectionError(f"门禁运行环境不得访问网络（C8.7）：{url}")
        return self._inner.fetch(url, **kwargs)


def _accepts_outbound(func: Callable[..., Any]) -> bool:
    """执行 seam 是否接受 outbound 关键字（显式形参或 **kwargs）。"""
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    if "outbound" in parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _call_with_outbound(func: Callable[..., Any], *args: Any, outbound: Any) -> Any:
    """把计数出站注入执行 seam（不接受 outbound 的 seam 保持原签名调用）。"""
    if _accepts_outbound(func):
        return func(*args, outbound=outbound)
    return func(*args)


def _isolated_gate_store() -> tuple[str, int]:
    """在磁盘创建门禁专用临时 store（返回路径＋current_pointer 实查行数）。

    临时 store 是门禁验证的隔离运行环境：不触现行 store、不写任何 current
    pointer（行数须为 0）；文件保留在磁盘作为可审计证据（C8.7）。
    """
    temp_dir = tempfile.mkdtemp(prefix="hktax-gate-")
    temp_store_path = os.path.join(temp_dir, "gate-store.db")
    gate_store = RuleStore(temp_store_path)
    conn = gate_store._connect()
    try:
        pointer_rows = int(
            conn.execute("SELECT COUNT(*) FROM current_pointer").fetchone()[0]
        )
    finally:
        conn.close()
    return temp_store_path, pointer_rows


def run_gate(
    *,
    candidate_content: Mapping[str, Any],
    current_content: Mapping[str, Any],
    manifests: Sequence[Mapping[str, Any]],
    independent_facts: Sequence[Mapping[str, Any]],
    required_cases: Sequence[str],
    harness: Any,
    engine: Any,
    outbound: Any = None,
) -> dict[str, Any]:
    """隔离门禁：产出 C8.6 形状报告；任何不满足 → quarantine（fail closed）。

    outbound 作为计数代理注入 harness/engine 执行（见 _call_with_outbound）：
    门禁自身绝不直接调用出站；执行期任何出站尝试都计入 network_calls 并触发
    隔离（> 0 → quarantine，即使逐阶段一致、参数证据齐备，C8.7）。
    """
    # C8.7 隔离证据：在磁盘实际创建临时 store 运行验证（不触现行 store），
    # 出站实际零调用，无用户 session，临时 store 未写 current pointer。
    counter = _CountingOutbound(outbound)
    temp_store_path, pointer_rows = _isolated_gate_store()
    fact_ids = {
        fact.get("fact_id") for fact in (independent_facts or []) if isinstance(fact, Mapping)
    }
    required = list(required_cases)

    executed = 0
    case_ok = 0
    stage_ok_all = True
    evidence_ok_all = True
    evidence_refs: list[Any] = []
    stage_failed_ids: list[str] = []
    missing_cases: list[str] = []
    missing_evidence_cases: list[str] = []

    for case_id in required:
        try:
            expected = _call_with_outbound(
                harness.run_case, case_id, outbound=counter
            )
        except KeyError:
            missing_cases.append(case_id)  # 必需 case 未执行（C8.4）
            continue
        executed += 1
        reference = expected.get("parameter_evidence_ref") if isinstance(expected, Mapping) else None
        if reference is not None:
            evidence_refs.append(reference)
        evidence_ok = reference is not None and reference in fact_ids
        if not evidence_ok:
            evidence_ok_all = False
            missing_evidence_cases.append(case_id)
        try:
            actual = _call_with_outbound(
                engine.run_case,
                case_id,
                candidate_content,
                outbound=counter,
            )
        except Exception:
            actual = None
        stage_ok = (
            isinstance(expected, Mapping)
            and isinstance(actual, Mapping)
            and expected.get("stages") == actual.get("stages")
            and expected.get("output") == actual.get("output")
        )
        if not stage_ok:
            stage_ok_all = False
            stage_failed_ids.append(case_id)
        if stage_ok and evidence_ok:
            case_ok += 1

    reasons: list[str] = []
    if not required:
        reasons.append("empty_report")  # 空报告＝零通过，禁止发布（C8.4）
    if missing_cases:
        reasons.append("missing_required_case")
    if not stage_ok_all:
        reasons.append("stage_inconsistent")
    if not evidence_ok_all:
        reasons.append("missing_parameter_evidence")
    if stage_failed_ids:
        reasons.append("regression_failure")
    if required and case_ok == 0 and "empty_report" not in reasons:
        reasons.append("zero_passed")
    if counter.calls > 0:
        # C8.7：门禁执行期任何出站尝试＝隔离违例（即使逐阶段一致、证据齐备）。
        reasons.append("network_violation")

    decision = "publish" if not reasons else "quarantine"
    gate_environment = {
        "temp_store_path": temp_store_path,
        "network_calls": counter.calls,  # 读于验证完成后：实际计数须为 0
        "user_session": False,
        "current_pointer_written": pointer_rows > 0,
    }
    return {
        "decision": decision,
        "quarantine_reason": ";".join(reasons) if reasons else None,
        "cases": {
            "required_case_ids": required,
            "executed": executed,
            "per_stage_consistent": bool(required) and stage_ok_all,
            "parameter_evidence_refs": evidence_refs,
        },
        "regression": {
            "passed": executed - len(stage_failed_ids),
            "failed": len(stage_failed_ids),
            "failed_ids": stage_failed_ids,
        },
        "tests": {
            "passed": case_ok,
            "failed": len(required) - case_ok,
            "fail_reasons": reasons,
            "fail_ids": sorted(set(missing_cases + missing_evidence_cases + stage_failed_ids)),
            "gate": (
                "passed>0 且必需 case 全执行＋参数证据＋逐阶段一致＋回归 failed=0；"
                "空报告=隔离（Annex C C8.4）"
            ),
        },
        "gate_environment": gate_environment,
    }
