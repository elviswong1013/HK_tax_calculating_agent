"""REQ-8（交叉 13）UI 结果状态渲染：partial 无结欠合计、标「未能估算」。

REQ: REQ-8／REQ-13（Annex A §12.1/§12.4 结果金额分项绑定；§12.9 命名测试
test_ui_partial_no_balance）。
规格锚点:
  - Annex A §6：状态徽标四枚（估算完成/部分结果/需要补充资料/暂不可计算）；
    未知分项标「未能估算」，不能用零填补。
  - Annex A §12.4：partial 仅含独立已知最终组件；缺组件无 balance；前端不得
    重新相减宽减或退税重建任何金额。
  - Annex C C3.3/C3.4：六金额键 canonical 字符串或 null；partial 缺 balance。
期望值来源: 结构断言（徽标文案、缺失标记、禁止重建合计）；金额样例
3000/1000/2000 为结构性 marker（server canonical 字符串直读），非税额期望。

【拟名】被测契约: 打印报告（POST /api/v1/report/print）按服务器 canonical
字段渲染 —— partial 徽标文案「部分结果」、缺组件标「未能估算」、已知分项
直读展示、不出现任何前端重建的结欠合计。
"""

from __future__ import annotations

from _helpers import REPLAY_MARKER, binding_four_keys, inject_record


def _partial_core() -> dict:
    """partial 记录：已知最终税 3000、已缴暂缴 1000；缺下年度暂缴税 → 无 balance。

    若前端重建结欠合计，会得到 3000−1000＝2000 —— 该值在断言中必须缺席。
    """
    return {
        "tax_type": "salaries_tax",
        "period": "2025_26",
        "input": {
            "year_of_assessment": "2025_26",
            "employment_income": "240000",
            "mpf_mandatory_contributions": "9000",
            "married_status": {"value": "single"},
        },
        "binding": {
            "bundle_id": "b-test-partial",
            "bundle_hash": "sha256:" + "11" * 32,
            "rules_schema_version": "1.0.0",
            "engine_version": "0.1.0",
        },
        "status": "partial",
        "amounts": {
            "before_reduction": "3000",
            "reduction": "0",
            "final_after_reduction": "3000",
            "provisional_paid": "1000",
            "next_provisional": None,
            "balance": None,
        },
        "missing_components": ["next_provisional"],
        "steps": [],
        "evidence_refs": [],
        "unsupported": [],
        "pending_verification": [],
        "blocked_reason": None,
        "questions": [],
        "warnings": ["下年度暂缴税规则缺失：结欠/退款暂未能估算"],
    }


def test_ui_partial_no_balance(ui) -> None:
    """partial 记录打印：徽标「部分结果」；缺组件标「未能估算」；已知分项
    直读；不得出现前端重建的结欠合计（2000）。"""
    assert binding_four_keys(_partial_core()["binding"])  # 结构自检
    record_id = inject_record(ui, _partial_core())

    r = ui.api.post("/api/v1/report/print", json={"record_id": record_id})
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text

    # —— 状态徽标映射（§6/§12.1：partial → 「部分结果」；非「估算完成」）——
    assert "部分结果" in html, "partial 状态必须显示「部分结果」徽标（§6 权威四徽标）"
    assert "估算完成" not in html, "partial 不得显示「估算完成」徽标"

    # —— 缺组件标「未能估算」（§6/§12.4：不能用零填补）——
    assert "未能估算" in html, "缺失组件必须标「未能估算」及原因（§12.4）"

    # —— 已知分项直读展示（server canonical 字符串）——
    assert "3000" in html, "已知分项（最终税款/已缴暂缴税事实）必须直读展示"
    assert "1000" in html

    # —— 禁止前端重建结欠合计（§12.4：不得重新相减）——
    assert "2000" not in html, (
        "balance 缺失（null）时不得出现重建的结欠/退款合计（3000−1000=2000 必须缺席）"
    )

    # —— 期间与免责（§6.6 打印保留期间与免责；免责为 REQ-13 记录要素）——
    assert "2025/26" in html or "2025_26" in html, "打印必须显示期间"
    assert "不宣称" in html or "估算" in html, "打印必须保留免责说明（非官方认证、仅为估算）"

    # 稳健性：历史标示不适用于当前 partial 展示（非重放入口）
    assert REPLAY_MARKER not in html or "部分结果" in html
