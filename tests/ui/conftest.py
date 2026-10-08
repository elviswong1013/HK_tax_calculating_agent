"""M6 UI 测试共享基建（tests/ui/；SDD §8 布局）。

REQ: 无独立 REQ（纯基建；服务 REQ-12/13 UI 测试文件，见各文件头部）。
规格锚点:
  - Annex A §12（UI/UX 设计规格；§12.9 命名测试计划）。
  - Annex C C1（API/单元测试经 httpx.ASGITransport 进程内执行；假件为进程内
    实现、不新增第三方库）。
  - Annex C C5.2/C5.3（不安全方法须 Origin＋CSRF；token 由服务器持有）。
期望值来源: 结构性契约，无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 与 tests/conftest.py 的 api fixture 同构，但额外暴露 app 把手
    （app.state.registry／app.state.store），供测试注入记录、发布新规则束、
    断言 epoch——这些是 UI 行为测试的服务端接缝，非生产入口。
  - 本模块对 app.* 一律延迟导入（fixture 内 import），使 Red 阶段每个测试的
    失败原因对应「缺失的 app.* 行为」，而非收集期整体失败。
"""

from __future__ import annotations

import httpx
import pytest

BASE_ORIGIN = "http://127.0.0.1:8000"


class UiApi:
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


class UiHarness:
    """app 把手＋回环 API 封装（进程内）。"""

    def __init__(self, app, api: UiApi) -> None:
        self.app = app
        self.api = api

    @property
    def registry(self):
        return self.app.state.registry

    @property
    def store(self):
        return self.app.state.store

    def session_id(self) -> str:
        """解析当前客户端会话 sid（先经安全 GET 建立会话）。"""
        from app.config import SESSION_COOKIE_NAME

        self.api._token()  # 确保 GET / 已建立会话 cookie
        raw = self.api.client.cookies.get(SESSION_COOKIE_NAME)
        sid = self.registry.sid_from_cookie(raw)
        assert sid, "未能解析测试客户端会话 sid（C5.3 不透明会话）"
        return sid


@pytest.fixture
def ui(tmp_path, monkeypatch):
    """进程内 ASGI 应用＋把手（每测试独立临时 DB；延迟导入 app.*）。"""
    monkeypatch.setenv("HKTAX_DB_PATH", str(tmp_path / "rules.db"))
    from app.main import create_app  # 延迟导入：Red 失败原因=缺失 app 行为

    app = create_app()
    client = httpx.Client(
        transport=httpx.ASGITransport(app=app), base_url=BASE_ORIGIN
    )
    yield UiHarness(app, UiApi(client))
    client.close()
