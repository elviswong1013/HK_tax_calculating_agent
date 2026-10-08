"""REQ-15/18 门禁执行期网络调用计数与反事实证明（第二轮 REJECTED 缺口 Red）。

REQ: REQ-15（在候选 bundle 上运行受影响＋回归测试）／REQ-18（门禁在隔离
环境运行——无网络；隔离违例 → 禁止发布）。
验收发现锚点（终验第二轮 REJECTED）:
  - app/updater/testgate.run_gate：_CountingOutbound 虽被创建，但从未注入
    harness/engine 执行——计数器计不到执行期出站（counterfactual 不可
    揭示）；网络违例也不改变 decision（stages/输出一致即 publish）。
规格锚点:
  - Annex C C8.7：门禁在临时 store 执行——无网络、无用户 session、不写
    current pointer；发布只发生在门禁外。
  - Annex C C8.4：门禁不满足 → 隔离，不得发布（fail closed）。
期望值来源: 结构性契约（计数/隔离判定/临时 store 实查）；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - run_gate 须把计数出站（包装 outbound 的代理）注入 harness 与 engine
    的执行（如 run_case(case_id, outbound=counter)／
    run_case(case_id, candidate_content, outbound=counter) 等价接缝）；
    执行期任何出站尝试都被计入 gate_environment.network_calls，且
    network_calls > 0 → decision 必为 quarantine（含网络违例隔离原因），
    即使逐阶段一致、参数证据齐备。
  - gate_environment.temp_store_path 指向门禁实际使用的临时 store：
    执行后磁盘可查（SQLite 表在），且 current_pointer 零行（未写指针）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from _acc_helpers import (
    BUDGET_MANIFEST,
    FACT_CAP_3500,
    GATE_CASE,
    FakeOutbound,
    changed_bundle_content,
    initial_content,
)

# 假出站放行的「证据 URL」——执行期网络尝试经此放行 1 次/调用点
REFERENCE_URL = "https://reference.invalid/independent-evidence"


class _LeakyHarness:
    """独立参考 harness：结果与候选一致，但 run_case 期间经注入出站取证据
    （执行期出站＝隔离违例，counterfactual 揭示用）。"""

    def run_case(self, case_id: str, **kwargs):
        outbound = kwargs.get("outbound")
        if outbound is not None:
            outbound.fetch(REFERENCE_URL)  # 假出站放行该次调用
        return {
            "case_id": case_id,
            "stages": ["3500"],
            "output": "3500",
            "parameter_evidence_ref": FACT_CAP_3500["fact_id"],
        }


class _LeakyEngine:
    """候选引擎：输出与 harness 一致，但执行期同样出站一次。"""

    def run_case(self, case_id: str, candidate_content, **kwargs):
        outbound = kwargs.get("outbound")
        if outbound is not None:
            outbound.fetch(REFERENCE_URL)
        return {"case_id": case_id, "stages": ["3500"], "output": "3500"}


class _QuietHarness:
    """对照 harness：不出站（除此以外与 _LeakyHarness 完全一致）。"""

    def run_case(self, case_id: str, **kwargs):
        return {
            "case_id": case_id,
            "stages": ["3500"],
            "output": "3500",
            "parameter_evidence_ref": FACT_CAP_3500["fact_id"],
        }


class _QuietEngine:
    """对照引擎：不出站（除此以外与 _LeakyEngine 完全一致）。"""

    def run_case(self, case_id: str, candidate_content, **kwargs):
        return {"case_id": case_id, "stages": ["3500"], "output": "3500"}


def _run_gate(harness, engine):
    from app.updater.testgate import run_gate  # 延迟导入（同目录约定）

    return run_gate(
        candidate_content=changed_bundle_content("3500"),
        current_content=initial_content(),
        manifests=[BUDGET_MANIFEST],
        independent_facts=[FACT_CAP_3500],
        required_cases=[GATE_CASE],
        harness=harness,
        engine=engine,
        outbound=FakeOutbound({REFERENCE_URL: b"evidence-bytes"}),
    )


def test_acceptance_testgate_counterfactual_network_attempt_blocks() -> None:
    """门禁执行期发生网络调用（假出站放行）→ 计入 network_calls 且禁止
    publish（counterfactual：同样输入换成不出站的 harness/engine 即
    publish）；temp_store_path 为门禁实际使用的 store（执行后实查
    current_pointer 未写）。"""
    # —— 违例场景：harness＋engine 执行期各出站一次，其余条件全部满足 ——
    report = _run_gate(_LeakyHarness(), _LeakyEngine())
    gate_environment = report.get("gate_environment") or {}

    network_calls = gate_environment.get("network_calls")
    assert network_calls == 2, (
        "C8.7：门禁须把计数出站注入 harness/engine 执行——执行期出站尝试"
        "（harness 1 次＋engine 1 次）必须计入 network_calls；实际 "
        f"{network_calls!r}（计数未覆盖执行期出站，counterfactual 不可揭示）"
    )
    assert report.get("decision") == "quarantine", (
        "C8.4/C8.7：门禁执行期网络违例 → 禁止发布（即使逐阶段一致、参数"
        f"证据齐备），实际 decision={report.get('decision')!r}"
    )
    assert report.get("quarantine_reason"), (
        "网络违例须体现为非空隔离原因（可审计）："
        f"{report.get('quarantine_reason')!r}"
    )

    # —— 临时 store 实查：路径存在、SQLite 表在、current_pointer 零行 ——
    temp_store_path = gate_environment.get("temp_store_path")
    assert isinstance(temp_store_path, str) and temp_store_path.strip(), (
        f"gate_environment 须携带真实临时 store 路径：{gate_environment!r}"
    )
    assert Path(temp_store_path).exists(), (
        f"临时 store 须真实存在于磁盘（可审计证据）：{temp_store_path!r}"
    )
    conn = sqlite3.connect(temp_store_path)
    try:
        pointer_rows = int(
            conn.execute("SELECT COUNT(*) FROM current_pointer").fetchone()[0]
        )
    finally:
        conn.close()
    assert pointer_rows == 0, (
        "门禁临时 store 不得写 current pointer（发布只发生在门禁外，C8.7）；"
        f"实际 current_pointer 行数 {pointer_rows}"
    )

    # —— counterfactual 对照：唯一差异＝执行期出站；不出站 → publish ——
    quiet_report = _run_gate(_QuietHarness(), _QuietEngine())
    assert quiet_report.get("gate_environment", {}).get("network_calls") == 0, (
        "对照（不出站）须计 0："
        f"{quiet_report.get('gate_environment')!r}"
    )
    assert quiet_report.get("decision") == "publish", (
        "counterfactual：同样输入下不出站的 harness/engine 应通过门禁"
        f"（既有绿行为），实际 {quiet_report.get('decision')!r}——"
        "两报告唯一差异须是执行期网络尝试"
    )
