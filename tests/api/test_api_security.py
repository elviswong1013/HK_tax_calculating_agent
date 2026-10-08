"""REQ-14 安全会话基线：回环绑定、Host/Origin 精确匹配、敏感操作 CSRF 能力。

REQ: REQ-14（SDD §9：本地计算独立于模型…；§7 安全中间件）。
规格锚点:
  - Annex C C5.1：仅绑定 127.0.0.1:<port>；受控启动入口校验绑定地址 ∈
    {127.0.0.1, localhost}，非回环绑定拒绝启动；合法 Host 精确集合
    127.0.0.1:<port>｜localhost:<port>；合法 Origin http://127.0.0.1:<port>｜
    http://localhost:<port> 且必须与 Host 一致。
  - Annex C C5.2：不安全方法（POST/PUT/PATCH/DELETE）Origin 缺失/null/跨源 →
    一律拒绝；且必须携带服务器 CSRF token；安全 GET：Host/会话校验无副作用；
    不信任 X-Forwarded-*；无 CORS。
  - Annex C C5.3：CSRF token 由服务器持有。
  - SDD §7：敏感操作（calc/check/publish/clear/rollback）必须携带 token。
  - SDD §4/C3.1：/api/v1/session DELETE＝清空会话（敏感 clear）。
  - Annex C C3.7：E_CSRF／E_ORIGIN／E_HOST → 403。
期望值来源: 纯安全行为断言；无金额数值期望。

【拟名】被测契约:
  - app.web.security.validate_bind_host(host)：{127.0.0.1, localhost} 精确集合
    通过；其余（含 0.0.0.0、::1、公网/域名）抛 app.web.security.LoopbackBindError。
  - CSRF 能力发放/回呈契约见 tests/conftest.py（安全 GET → X-CSRF-Token）。
"""

from __future__ import annotations

import pytest

from app.web.security import LoopbackBindError, validate_bind_host

VALID_HOSTS = ["127.0.0.1", "localhost"]
INVALID_HOSTS = [
    "0.0.0.0",
    "192.168.1.5",
    "::",
    "::1",  # 不在 C5.1 精确集合内
    "example.com",
    "",
]


def test_loopback_only_refuses_public_bind() -> None:
    """C5.1：非回环绑定拒绝启动（精确集合 {127.0.0.1, localhost}）。"""
    for host in VALID_HOSTS:
        validate_bind_host(host)  # 不得抛异常

    for host in INVALID_HOSTS:
        with pytest.raises(LoopbackBindError):
            validate_bind_host(host)


def test_host_origin_exact_match_required(raw_client) -> None:
    """C5.1/C5.2：Host 精确匹配、Origin 须与 Host 一致、X-Forwarded-* 不信任。"""
    # —— 合法 Host 的安全 GET 正常 ——
    ok = raw_client.get("/api/v1/meta/version")
    assert ok.status_code == 200, ok.text

    # —— Host 不在合法精确集合 → 403 E_HOST ——
    bad_host = raw_client.get(
        "/api/v1/meta/version", headers={"Host": "evil.example:8000"}
    )
    assert bad_host.status_code == 403, bad_host.text
    assert bad_host.json()["error"]["code"] == "E_HOST"

    # —— X-Forwarded-* 不信任：伪转发头不得改变 Host 判定（C5.2）——
    forwarded = raw_client.get(
        "/api/v1/meta/version", headers={"X-Forwarded-Host": "evil.example"}
    )
    assert forwarded.status_code == 200, forwarded.text

    # —— 不安全方法：Origin 缺失 / null / 跨源 → 403 E_ORIGIN ——
    # （携带有效 CSRF，隔离 Origin 维度的拒绝原因）
    bootstrap = raw_client.get("/")
    assert bootstrap.status_code == 200
    token = bootstrap.headers.get("X-CSRF-Token")
    assert token, "安全 GET 须发放 X-CSRF-Token（conftest 拟名契约）"
    base_headers = {"X-CSRF-Token": token}

    no_origin = raw_client.post(
        "/api/v1/calc/prepare", json={}, headers=dict(base_headers)
    )
    assert no_origin.status_code == 403, no_origin.text
    assert no_origin.json()["error"]["code"] == "E_ORIGIN"

    null_origin = raw_client.post(
        "/api/v1/calc/prepare",
        json={},
        headers={**base_headers, "Origin": "null"},
    )
    assert null_origin.status_code == 403
    assert null_origin.json()["error"]["code"] == "E_ORIGIN"

    cross_origin = raw_client.post(
        "/api/v1/calc/prepare",
        json={},
        headers={**base_headers, "Origin": "http://evil.example"},
    )
    assert cross_origin.status_code == 403
    assert cross_origin.json()["error"]["code"] == "E_ORIGIN"

    # —— Origin 与 Host 不一致（同为合法回环词但不匹配）→ 403（C5.1）——
    mismatch = raw_client.post(
        "/api/v1/calc/prepare",
        json={},
        headers={**base_headers, "Origin": "http://localhost:8000"},
    )
    assert mismatch.status_code == 403, mismatch.text


def test_sensitive_actions_require_csrf_capability(api) -> None:
    """C5.2/§7：敏感操作（calc prepare、清空会话）必须携带服务器 CSRF 能力。"""
    # —— 无 token → 403 E_CSRF（Origin 合法，隔离 CSRF 维度）——
    no_token = api.post("/api/v1/calc/prepare", json={}, csrf=False)
    assert no_token.status_code == 403, no_token.text
    assert no_token.json()["error"]["code"] == "E_CSRF"

    wrong_token = api.post(
        "/api/v1/calc/prepare",
        json={},
        csrf=False,
        headers={"X-CSRF-Token": "forged-token"},
    )
    assert wrong_token.status_code == 403
    assert wrong_token.json()["error"]["code"] == "E_CSRF"

    clear_no_token = api.delete("/api/v1/session", csrf=False)
    assert clear_no_token.status_code == 403, clear_no_token.text
    assert clear_no_token.json()["error"]["code"] == "E_CSRF"

    # —— 正确 token：通过 CSRF 门（后续按业务校验给 201/422，但绝不能是 403）——
    valid_body = {
        "tax_type": "salaries_tax",
        "schema_version": "1.0.0",
        "input": {
            "year_of_assessment": "2025_26",
            "employment_income": "240000",
            "mpf_mandatory_contributions": "0",
            "married_status": {"value": "single"},
        },
    }
    with_token = api.post("/api/v1/calc/prepare", json=valid_body)
    assert with_token.status_code != 403, with_token.text
