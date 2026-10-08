"""共享测试基建（M1 Red 阶段；tests/ 按 SDD §8 布局）。

REQ: 无独立 REQ（纯基建；服务各 API 层测试文件，见各文件头部）。
规格锚点:
  - Annex C C1（测试接线：API/单元测试经 httpx.ASGITransport 进程内执行；
    假件为进程内实现、不新增第三方库）。
  - SDD §8（DB 运行时默认 %LOCALAPPDATA%\\hktax-agent\\rules.db，HKTAX_DB_PATH 可覆盖；
    仓库内无 DB —— 测试一律注入临时路径）。
  - Annex C C5.1/C5.2/C5.3（合法 Host/Origin 精确集合、不安全方法须 Origin+CSRF、
    CSRF token 由服务器持有）。
期望值来源: 结构性契约，无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.main.create_app() -> ASGI app；DB 路径取 env HKTAX_DB_PATH（测试注入 tmp）。
  - 任意同源安全 GET（如 GET /）建立会话 cookie 的同时，经响应头 X-CSRF-Token
    发放 CSRF 能力；不安全方法（POST/PUT/PATCH/DELETE）须以同名请求头回呈该
    token，且 Origin 与 Host 完全一致（C5.2）。
  - 本模块对 app.* 一律延迟导入（fixture 内 import），使 Red 阶段每个测试的
    失败原因对应「缺失的 app.* 模块」，而非收集期整体失败。
"""

from __future__ import annotations

import httpx
import pytest

REPO_ROOT_DIR = None  # 占位：由各测试文件按 __file__ 自行解析，避免本文件被误扫
BASE_ORIGIN = "http://127.0.0.1:8000"


class Api:
    """在合法回环 Host/Origin 下带 CSRF 能力访问 API 的薄封装。"""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._csrf_token: str | None = None

    def _token(self) -> str:
        if self._csrf_token is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, (
                "安全 GET 未发放 X-CSRF-Token（C5.2/C5.3 拟名契约："
                "会话建立时经响应头发放 CSRF 能力）"
            )
            self._csrf_token = token
        return self._csrf_token

    def get(self, path: str, *, headers: dict | None = None):
        return self._client.get(path, headers=headers)

    def post(
        self,
        path: str,
        *,
        json: object | None = None,
        origin: str | None = BASE_ORIGIN,
        csrf: bool = True,
        headers: dict | None = None,
    ):
        h = dict(headers or {})
        if origin is not None:
            h["Origin"] = origin
        if csrf:
            h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json, headers=h)

    def delete(
        self,
        path: str,
        *,
        origin: str | None = BASE_ORIGIN,
        csrf: bool = True,
        headers: dict | None = None,
    ):
        h = dict(headers or {})
        if origin is not None:
            h["Origin"] = origin
        if csrf:
            h["X-CSRF-Token"] = self._token()
        return self._client.delete(path, headers=h)


@pytest.fixture
def api(tmp_path, monkeypatch):
    """进程内 ASGI 应用 + 合法回环客户端（每测试独立临时 DB）。"""
    monkeypatch.setenv("HKTAX_DB_PATH", str(tmp_path / "rules.db"))
    from app.main import create_app  # 延迟导入：Red 失败原因=缺失 app 模块

    app = create_app()
    client = httpx.Client(
        transport=httpx.ASGITransport(app=app), base_url=BASE_ORIGIN
    )
    yield Api(client)
    client.close()


@pytest.fixture
def raw_client(tmp_path, monkeypatch):
    """不经封装的回环客户端（安全测试需要完全控制请求头）。"""
    monkeypatch.setenv("HKTAX_DB_PATH", str(tmp_path / "rules.db"))
    from app.main import create_app  # 延迟导入：Red 失败原因=缺失 app 模块

    app = create_app()
    client = httpx.Client(
        transport=httpx.ASGITransport(app=app), base_url=BASE_ORIGIN
    )
    yield client
    client.close()
