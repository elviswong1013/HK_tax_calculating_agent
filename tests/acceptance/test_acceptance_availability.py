"""REQ-17/18 计算须遵守规则可用性守卫（终验 REJECTED 缺口 Red）。

REQ: REQ-17（更新调度与来源状态）／REQ-18（降级行：离线时已验证快照可计算
＋中文 notice；已知变更/过期 → 拒绝受影响期间）。
验收发现锚点（终验 REJECTED）:
  - app/api/routes.py 的 prepare/execute 全链路从未调用
    app.state.update_scheduler.availability(period)：SOURCE 已进入
    known-change/expired（受影响期间 availability=blocked）后计算仍照常
    complete；trusted_offline 期间可算但无任何离线 notice 告警。
规格锚点:
  - SDD §5 降级行＋Annex C C7.9（SOURCE 状态 enter/clear；到期≠过期）；
    C3.7 E_RULES_STATE_UNVERIFIABLE → 422。
  - 状态 enter 经真实调度管线（预算来源 manifest legal_state_rules 判
    expired→2024_25；与 tests/updater 既有绿测试同形宇宙），计算侧经
    app.state.update_scheduler 接缝（同 tests/ui 注假件模式）。
期望值来源: 结构/状态断言（拒算错误码、blocked 记录金额全 null、
  trusted_offline 可算＋中文 notice）；无金额数值期望。

Red 说明: 当前路由不咨询 availability——场景 A 中 2024_25（expired）全链路
  仍 complete（失败原因＝「受影响期间不得输出金额结果」）；场景 B 中
  trusted_offline 可算但 warnings 为空（失败原因＝「缺离线 notice」）。
"""

from __future__ import annotations

import re

from _acc_helpers import (
    BUDGET_DOC_EXPIRED_NOTICE,
    BUDGET_DOC_OK,
    BUDGET_URL,
    FakeClock,
    FakeOutbound,
    confirm,
    error_code,
    execute,
    hk,
    make_scheduler,
    prepare,
    salaries_min_facts,
)
from _acc_helpers import TAX_EXECUTE_TARGETS as _TARGETS

_SALARIES_TARGET = _TARGETS["salaries_tax"]


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def test_acceptance_calculation_respects_availability_guards(make_acc) -> None:
    """SOURCE 状态 known-change/expired 期间 → 受影响期间计算被拒
    （E_RULES_STATE_UNVERIFIABLE 或 blocked 金额全 null；未受影响期间不泛化
    阻断）；trusted_offline → 可算且带中文离线 notice。"""
    # ================================================================
    # 场景 A：expired（官方证据标注失效，2024_25 受影响）→ 计算被拒
    # ================================================================
    acc = make_acc("expired")
    scheduler = make_scheduler(
        acc.store,
        clock=FakeClock(hk(2026, 3, 2)),
        outbound=FakeOutbound({BUDGET_URL: BUDGET_DOC_EXPIRED_NOTICE}),
    )
    acc.app.state.update_scheduler = scheduler  # 路由层接缝（同 tests/ui 模式）
    job = scheduler.startup()
    # —— 前置自证：状态确已 enter（分类结果，非来源失败；既有绿行为）——
    assert job is not None and job["status"] == "succeeded", (
        f"expired 通知是分类结果（检查应完成）：{job!r}"
    )
    assert "expired" in scheduler.source_states()["ird_budget"], (
        f"前置自证失败：expired 状态未 enter：{scheduler.source_states()!r}"
    )
    assert scheduler.availability("2024_25")["mode"] == "blocked", (
        "前置自证失败：2024_25 应 blocked"
    )

    r_prepare = prepare(acc.api, "salaries_tax", salaries_min_facts(year="2024_25"))
    if r_prepare.status_code == 201:
        r_confirm = confirm(acc.api, r_prepare.json())
        assert r_confirm.status_code == 200, r_confirm.text[:200]
        r_execute = execute(
            acc.api, r_confirm.json()["confirmation_id"], _SALARIES_TARGET
        )
        rejected = (
            r_execute.status_code != 200
            and error_code(r_execute) == "E_RULES_STATE_UNVERIFIABLE"
        )
        blocked_record = False
        if r_execute.status_code == 200:
            payload = r_execute.json()
            blocked_record = payload.get("status") == "blocked" and all(
                value is None for value in (payload.get("amounts") or {}).values()
            )
        assert rejected or blocked_record, (
            "REQ-18：expired 期间（2024_25）受影响期间的现行计算必须被拒"
            "（E_RULES_STATE_UNVERIFIABLE 或 blocked 金额全 null）——当前仍照常"
            f"输出结果：execute={r_execute.status_code} "
            f"{r_execute.text[:300]!r}"
        )
    else:
        assert error_code(r_prepare) == "E_RULES_STATE_UNVERIFIABLE", (
            "REQ-18：expired 期间（2024_25）prepare 须以 "
            f"E_RULES_STATE_UNVERIFIABLE 拒算，实际 {r_prepare.status_code} "
            f"{error_code(r_prepare)!r}: {r_prepare.text[:200]}"
        )

    # —— 对照：未受影响期间（2025_26）不得泛化阻断 ——
    r_ok = prepare(acc.api, "salaries_tax", salaries_min_facts(year="2025_26"))
    assert r_ok.status_code == 201, (
        "未受影响期间（2025_26 availability=ok）不得泛化阻断，实际 "
        f"{r_ok.status_code}: {r_ok.text[:200]}"
    )

    # ================================================================
    # 场景 B：trusted_offline（已核验快照在档＋网络不可达）→ 可算＋notice
    # ================================================================
    acc2 = make_acc("offline")
    clock2 = FakeClock(hk(2026, 3, 2))
    outbound2 = FakeOutbound({BUDGET_URL: BUDGET_DOC_OK})
    scheduler2 = make_scheduler(acc2.store, clock=clock2, outbound=outbound2)
    acc2.app.state.update_scheduler = scheduler2
    assert scheduler2.startup() is not None  # 建立已核验快照基线
    clock2.set(hk(2026, 4, 2))
    outbound2.unreachable = True
    scheduler2.tick()  # 检查失败 → trusted_offline enter（C7.9）
    assert scheduler2.availability("2025_26")["mode"] == "trusted_offline", (
        f"前置自证失败：2025_26 应 trusted_offline："
        f"{scheduler2.availability('2025_26')!r}"
    )

    r2_prepare = prepare(
        acc2.api, "salaries_tax", salaries_min_facts(year="2025_26")
    )
    assert r2_prepare.status_code == 201, (
        f"trusted_offline 必须可算（已验证快照），prepare 实际 "
        f"{r2_prepare.status_code}: {r2_prepare.text[:200]}"
    )
    r2_confirm = confirm(acc2.api, r2_prepare.json())
    assert r2_confirm.status_code == 200, r2_confirm.text[:200]
    r2_execute = execute(
        acc2.api, r2_confirm.json()["confirmation_id"], _SALARIES_TARGET
    )
    assert r2_execute.status_code == 200, (
        f"trusted_offline 必须可算（REQ-18 降级行），execute 实际 "
        f"{r2_execute.status_code}: {r2_execute.text[:200]}"
    )
    payload2 = r2_execute.json()
    assert payload2.get("status") in ("complete", "partial"), (
        f"trusted_offline 期间按已验证快照正常计算，实际 {payload2.get('status')!r}"
    )
    notices = list(r2_prepare.json().get("warnings") or []) + list(
        payload2.get("warnings") or []
    )
    assert notices and any(
        _has_chinese(n) for n in notices if isinstance(n, str)
    ), (
        "REQ-18：trusted_offline 计算必须携带中文离线 notice（prepare 或 "
        "execute 任一 warnings 均可）——当前静默无告警；"
        f"prepare.warnings={r2_prepare.json().get('warnings')!r} "
        f"execute.warnings={payload2.get('warnings')!r}"
    )
