"""REQ-13/14 敏感响应缓存与内容安全指令（C5.7：no-store／inline／attachment）。

REQ: REQ-13（记录下载/打印）、REQ-14（C5.5 敏感响应 no-store）。
规格锚点:
  - Annex C C5.7 缓存与内容安全指令（权威表）：
      敏感响应（计算/记录/同意/更新）→ Cache-Control: no-store；
      打印（/api/v1/report/print）→ no-store＋Content-Disposition: inline；
      下载（/api/v1/records/{id}/download）→ no-store＋
      Content-Disposition: attachment。
  - Annex C C3.1：/api/v1/records/{record_id}（读取）与
    /api/v1/records/{record_id}/download（全量规范确认事实下载：no-store；
    attachment）。
期望值来源: 纯响应头契约断言；无金额数值期望。

【拟名】契约说明:
  - M1 骨架阶段记录尚未产生：以不存在的 record_id 触达路由，路由级响应头
    指令（no-store/Content-Disposition）对错误响应同样成立（中间件按路由
    注入），从而 M1 即可锁定头部契约、后续阶段不回退。
"""

from __future__ import annotations


def test_records_api_no_store_headers(api) -> None:
    """记录与计算路由的响应一律 no-store（C5.5/C5.7）。"""
    # —— 记录路由（M1 无记录 → 404/422，路由已接线且必须 no-store）——
    r = api.get("/api/v1/records/r-missing-m1")
    assert r.status_code in (200, 404, 422), r.text
    assert "no-store" in r.headers.get("cache-control", ""), (
        f"记录路由响应必须 no-store（C5.7），实际 cache-control="
        f"{r.headers.get('cache-control')!r}"
    )

    # —— 计算路由（业务 422 拒算响应）同样 no-store ——
    invalid_body = {
        "tax_type": "salaries_tax",
        "schema_version": "1.0.0",
        "input": {"year_of_assessment": "2025_26"},  # 缺金额事实 → 422
    }
    r2 = api.post("/api/v1/calc/prepare", json=invalid_body)
    assert r2.status_code == 422, r2.text
    assert "no-store" in r2.headers.get("cache-control", ""), (
        "计算路由响应必须 no-store（C5.7 敏感响应）"
    )

    # —— 会话清空（敏感 clear）——
    r3 = api.delete("/api/v1/session")
    assert r3.status_code < 500, r3.text
    assert "no-store" in r3.headers.get("cache-control", "")


def test_json_export_and_print_report_content_disposition(api) -> None:
    """打印＝inline＋no-store；记录 JSON 下载＝attachment＋no-store（C5.7）。"""
    # —— 打印报告：inline ——
    r_print = api.post("/api/v1/report/print", json={"record_id": "r-missing-m1"})
    # M1 无记录：404/422 皆可（记录不存在），但路由必须已接线（非 405/404 页面）
    assert r_print.status_code in (200, 404, 422), r_print.text
    assert "no-store" in r_print.headers.get("cache-control", "")
    disposition = r_print.headers.get("content-disposition", "")
    assert "inline" in disposition, (
        f"/api/v1/report/print 必须 Content-Disposition: inline（C5.7），"
        f"实际 {disposition!r}"
    )

    # —— 记录 JSON 导出（全量规范确认事实下载）：attachment ——
    r_download = api.get("/api/v1/records/r-missing-m1/download")
    assert r_download.status_code in (200, 404, 422), r_download.text
    assert "no-store" in r_download.headers.get("cache-control", "")
    disposition_dl = r_download.headers.get("content-disposition", "")
    assert "attachment" in disposition_dl, (
        f"/api/v1/records/{{id}}/download 必须 Content-Disposition: attachment"
        f"（C5.7），实际 {disposition_dl!r}"
    )
