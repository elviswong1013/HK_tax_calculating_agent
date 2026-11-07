"""安全批次B（M6）回归：模型端点策略 fail closed。

来源：2026-11-05 安全审查 ARCH-09/F-08（hktax-security-review-20261105）。
契约:
  - endpoint 为 https → 允许；
  - endpoint 为 http 且 host 属本机回环（127.0.0.1/localhost/::1）→ 允许（本地模型）；
  - 其他 scheme/host（含远程 http://）→ E_MODEL_UNCONFIGURED（本地计算照常可用）；
  - URL 内嵌 userinfo 凭据 → E_MODEL_UNCONFIGURED（密钥仅经 env＋Authorization 头）。
"""

from __future__ import annotations

import pytest

from app.api import consent
from app.core.errors import AppError


def _set_env(monkeypatch, endpoint: str | None) -> None:
    if endpoint is None:
        monkeypatch.delenv(consent.MODEL_ENDPOINT_ENV, raising=False)
    else:
        monkeypatch.setenv(consent.MODEL_ENDPOINT_ENV, endpoint)
    monkeypatch.setenv(consent.MODEL_NAME_ENV, "test-model")
    monkeypatch.setenv(consent.MODEL_API_KEY_ENV, "test-key")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.example.com/v1/chat/completions",
        "http://127.0.0.1:8000/v1",
        "http://localhost:11434/v1",
        "http://[::1]:9000/v1",
    ],
)
def test_allowed_endpoints_pass(monkeypatch, endpoint: str) -> None:
    _set_env(monkeypatch, endpoint)
    config = consent.model_config()
    assert config["upstream_endpoint"] == endpoint


def test_unconfigured_endpoint_is_none(monkeypatch) -> None:
    _set_env(monkeypatch, None)
    config = consent.model_config()
    assert config["upstream_endpoint"] is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.example.com/v1/chat/completions",
        "http://10.0.0.5:8080/v1",
        "ftp://example.com/model",
        "https://user:pass@api.example.com/v1",
        "https://ak-123@api.example.com/v1",
    ],
)
def test_disallowed_endpoints_fail_closed(monkeypatch, endpoint: str) -> None:
    _set_env(monkeypatch, endpoint)
    with pytest.raises(AppError) as excinfo:
        consent.model_config()
    assert excinfo.value.code == "E_MODEL_UNCONFIGURED", (
        f"违规端点必须 fail closed 为 E_MODEL_UNCONFIGURED：{endpoint!r}"
    )
