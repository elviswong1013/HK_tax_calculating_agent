"""M7 AI 集成测试共享基建（tests/ai/；SDD §8 布局）。

REQ: 无独立 REQ（纯基建；服务 REQ-11/14 AI 集成测试文件，见各文件头部）。
规格锚点:
  - Annex C C1（API/单元测试经 httpx.ASGITransport 进程内执行；假件为进程内
    实现、不新增第三方库）。
  - Annex C C4.2（配置仅服务端 env：HKTAX_MODEL_ENDPOINT/HKTAX_MODEL_NAME/
    HKTAX_MODEL_API_KEY；测试经 monkeypatch 注入）。
  - Annex C C4.6 ⑦段（外发经出站接缝；生产默认真实 HTTPX content=frozen_bytes）。
  - SDD §8（DB 测试一律注入临时路径）。
期望值来源: 结构性契约，无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.state.model_outbound（同 app.state.update_scheduler 的服务端接缝
    模式）：对象持 send(upstream_endpoint, api_key, frozen) -> {"status_code",
    "body"}；路由外发必须经此接缝（接缝存在时不得绕行真实网络）。
  - ai fixture：三 env 全配置＋假出站注入；ai_offline：三 env 全部清除
    （模型未配置态）。
  - 本模块对 app.* 一律延迟导入（fixture 内 import），使 Red 阶段每个测试的
    失败原因对应「缺失的 app.* 行为」，而非收集期整体失败。
"""

from __future__ import annotations

import httpx
import pytest

from _ai_fakes import (
    MODEL_API_KEY_ENV,
    MODEL_ENDPOINT_ENV,
    MODEL_NAME_ENV,
    TEST_API_KEY,
    TEST_MODEL_ENDPOINT,
    TEST_MODEL_NAME,
    FakeModelOutbound,
)

BASE_ORIGIN = "http://127.0.0.1:8000"


class AiApi:
    """在合法回环 Host/Origin 下带 CSRF 能力访问 API 的薄封装（同 tests/conftest）。"""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._csrf_token: str | None = None

    def _token(self) -> str:
        if self._csrf_token is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, "安全 GET 未发放 X-CSRF-Token（C5.2/C5.3）"
            self._csrf_token = token
        return self._csrf_token

    def get(self, path: str, *, headers: dict | None = None):
        return self._client.get(path, headers=headers)

    def post(self, path: str, *, json: object | None = None, headers: dict | None = None):
        h = dict(headers or {})
        h["Origin"] = BASE_ORIGIN
        h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json, headers=h)

    def delete(self, path: str, *, headers: dict | None = None):
        h = dict(headers or {})
        h["Origin"] = BASE_ORIGIN
        h["X-CSRF-Token"] = self._token()
        return self._client.delete(path, headers=h)

    @property
    def client(self) -> httpx.Client:
        return self._client


class AiHarness:
    """app 把手＋回环 API 封装＋假出站（进程内）。"""

    def __init__(self, app, api: AiApi, outbound: FakeModelOutbound) -> None:
        self.app = app
        self.api = api
        self.outbound = outbound

    @property
    def registry(self):
        return self.app.state.registry

    @property
    def store(self):
        return self.app.state.store

    def session_id(self) -> str:
        from app.config import SESSION_COOKIE_NAME

        self.api._token()  # 确保 GET / 已建立会话 cookie
        raw = self.api.client.cookies.get(SESSION_COOKIE_NAME)
        sid = self.registry.sid_from_cookie(raw)
        assert sid, "未能解析测试客户端会话 sid（C5.3 不透明会话）"
        return sid


def _build_app(tmp_path, monkeypatch, *, with_model_env: bool):
    monkeypatch.setenv("HKTAX_DB_PATH", str(tmp_path / "rules.db"))
    for var in (MODEL_ENDPOINT_ENV, MODEL_NAME_ENV, MODEL_API_KEY_ENV):
        monkeypatch.delenv(var, raising=False)
    if with_model_env:
        monkeypatch.setenv(MODEL_ENDPOINT_ENV, TEST_MODEL_ENDPOINT)
        monkeypatch.setenv(MODEL_NAME_ENV, TEST_MODEL_NAME)
        monkeypatch.setenv(MODEL_API_KEY_ENV, TEST_API_KEY)

    from app.main import create_app  # 延迟导入：Red 失败原因=缺失 app 行为

    app = create_app()
    fake = FakeModelOutbound()
    # 出站接缝（【拟名】）：路由外发必须经 app.state.model_outbound
    app.state.model_outbound = fake
    client = httpx.Client(
        transport=httpx.ASGITransport(app=app), base_url=BASE_ORIGIN
    )
    return app, fake, client


@pytest.fixture
def ai(tmp_path, monkeypatch):
    """模型已配置（三 env）＋假出站注入的进程内应用。"""
    app, fake, client = _build_app(tmp_path, monkeypatch, with_model_env=True)
    yield AiHarness(app, AiApi(client), fake)
    client.close()


@pytest.fixture
def ai_offline(tmp_path, monkeypatch):
    """模型未配置（三 env 全清除；C3.7 E_MODEL_UNCONFIGURED 态）。"""
    app, fake, client = _build_app(tmp_path, monkeypatch, with_model_env=False)
    yield AiHarness(app, AiApi(client), fake)
    client.close()
