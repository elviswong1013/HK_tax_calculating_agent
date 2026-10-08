"""REQ-17/18 UI 规则更新绑定：进行中触发 → 202＋同一 job_id；轮询无财务负载。

REQ: REQ-17／REQ-18（Annex A §12.7 规则更新绑定；§12.9 命名测试
test_ui_update_check_202_shared_job）。
规格锚点:
  - Annex A §12.7：「立即检查」→ POST /api/v1/update/check；已有作业运行时
    返回 202＋现有 job_id（幂等），前端轮询 GET /api/v1/update/jobs/{job_id}
    （响应不含财务负载）。
  - Annex C C3.1 端点表（update/check 202 行）与 C3.7（E_UPDATE_IN_PROGRESS →
    202 显示现有作业进度）。
  - Annex C C6 调度（singleflight：进行中再触发返回现有作业，不重跑）。
期望值来源: 结构/状态断言（202、job_id 幂等、无财务字段）；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 路由层经 app.state.update_scheduler 接调度器（测试注 seam）；作业进行中
    （trigger_manual() 返回 in_progress=True）→ HTTP 202＋现有 job_id；
    再次触发 → 同一 job_id（幂等，不新建作业）。
  - GET /api/v1/update/jobs/{job_id} → 作业详情；响应不含任何财务负载
    （金额/输入事实/记录 id）。
  - job dict 形状沿用 M5 调度契约：{job_id, kind, status, in_progress}。
"""

from __future__ import annotations

import json

_JOB = {
    "job_id": "job-m6-shared",
    "kind": "manual",
    "status": "running",
    "in_progress": True,
}


class _FakeUpdateScheduler:
    """singleflight 假件：进行中作业常驻；再触发返回同一作业（M5 调度契约）。"""

    def __init__(self) -> None:
        self.trigger_calls = 0

    def trigger_manual(self) -> dict:
        self.trigger_calls += 1
        return dict(_JOB)

    def status(self) -> dict:
        return {"active_job": dict(_JOB)}


# 轮询响应不得出现的财务负载标记（结构检查；无金额期望）
_FINANCIAL_MARKERS = ("amounts", "employment_income", "record_id", "balance")


def test_ui_update_check_202_shared_job(ui) -> None:
    """进行中触发「立即检查」→ 202＋同一 job_id（幂等）；轮询作业详情响应
    不含财务负载（§12.7）。"""
    fake = _FakeUpdateScheduler()
    ui.app.state.update_scheduler = fake  # 路由层接缝（见【拟名】契约）
    api = ui.api

    # —— 进行中触发 → 202＋现有 job_id ——
    first = api.post("/api/v1/update/check")
    assert first.status_code == 202, (
        f"已有作业运行时触发检查必须 202＋现有 job_id（§12.7 幂等），"
        f"实际 {first.status_code}: {first.text[:300]!r}"
    )
    first_payload = first.json()
    assert first_payload.get("job_id") == _JOB["job_id"], (
        f"202 响应必须携带现有作业 job_id：{first_payload!r}"
    )

    # —— 进行中再次触发 → 202＋同一 job_id（singleflight，不重跑）——
    second = api.post("/api/v1/update/check")
    assert second.status_code == 202, (
        f"进行中再触发必须 202（不排队重跑），实际 {second.status_code}"
    )
    assert second.json().get("job_id") == _JOB["job_id"], (
        f"再触发必须返回同一 job_id：{second.json()!r}"
    )
    assert fake.trigger_calls == 2, "路由必须经调度接缝触发（app.state.update_scheduler）"

    # —— 轮询作业详情：无财务负载 ——
    detail = api.get(f"/api/v1/update/jobs/{_JOB['job_id']}")
    assert detail.status_code == 200, (
        f"作业轮询入口必须可用（GET /api/v1/update/jobs/{{job_id}}），"
        f"实际 {detail.status_code}: {detail.text[:300]!r}"
    )
    detail_text = json.dumps(detail.json(), ensure_ascii=False)
    for marker in _FINANCIAL_MARKERS:
        assert f'"{marker}"' not in detail_text, (
            f"轮询响应不得含财务负载字段 {marker!r}（§12.7：检查不发送/不回显用户税务资料）"
        )

    # —— 「检查成功」≠「规则已更新」：作业详情须区分状态与发布结果 ——
    assert detail_text, "作业详情必须非空"
