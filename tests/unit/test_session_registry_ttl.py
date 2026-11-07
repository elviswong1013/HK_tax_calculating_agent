"""安全批次B（M3）回归：会话 TTL 与注册表容量上限。

来源：2026-11-05 安全审查 F-02/ARCH-05：SessionRegistry 无过期、无容量
上限（规格要求的闲置/绝对生命周期未实现），可达客户端可无限建会话耗内存。
契约（app/config.py 安全批次B 常量）:
  - 闲置 TTL 1800s：有效使用刷新 last_used；超时未用 → 下次按新会话处理；
  - 绝对生命周期 7200s：即使持续使用也过期；
  - 注册表容量 64：超容量按 last_used 淘汰最久未用会话。
"""

from __future__ import annotations

from app.web.security import SessionRegistry


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _registry() -> tuple[SessionRegistry, _FakeClock]:
    registry = SessionRegistry()
    clock = _FakeClock()
    registry._clock = clock
    return registry, clock


def test_idle_ttl_expires_unused_session() -> None:
    registry, clock = _registry()
    sid, cookie = registry.ensure_session(None)
    clock.advance(1799)
    same_sid, set_cookie = registry.ensure_session(cookie)
    assert same_sid == sid and set_cookie is None, "闲置未超时应复用会话"
    clock.advance(1801)  # 复用后再次闲置 1801s > 1800s 闲置 TTL → 过期
    new_sid, new_cookie = registry.ensure_session(cookie)
    assert new_sid != sid and new_cookie is not None, "闲置超时必须按新会话处理"
    assert sid not in registry._sessions, "过期会话必须从注册表移除"


def test_usage_refreshes_idle_ttl() -> None:
    registry, clock = _registry()
    sid, cookie = registry.ensure_session(None)
    for _ in range(5):  # 每 1000s 使用一次：单次间隔 < 1800s
        clock.advance(1000)
        same_sid, set_cookie = registry.ensure_session(cookie)
        assert same_sid == sid and set_cookie is None


def test_absolute_ttl_expires_even_with_use() -> None:
    registry, clock = _registry()
    sid, cookie = registry.ensure_session(None)
    for _ in range(7):  # 持续使用至 7000s（间隔 < 闲置 TTL）
        clock.advance(1000)
        registry.ensure_session(cookie)
    assert sid in registry._sessions, "7000s 内持续使用应仍有效"
    clock.advance(1000)  # 绝对生命周期 8000s > 7200s
    new_sid, new_cookie = registry.ensure_session(cookie)
    assert new_sid != sid and new_cookie is not None, (
        "绝对生命周期到期后即使持续使用也必须按新会话处理"
    )


def test_registry_capacity_caps_and_evicts_oldest() -> None:
    registry, clock = _registry()
    first_sid, _ = registry.ensure_session(None)
    created = [first_sid]
    for _ in range(70):  # 共 71 个会话，逐个隔 1s 创建
        clock.advance(1)
        sid, _ = registry.ensure_session(None)
        created.append(sid)
    assert len(registry._sessions) <= 64, "注册表必须受容量上限约束"
    assert first_sid not in registry._sessions, "最久未用会话必须被淘汰"
    for sid in created[-64:]:
        assert sid in registry._sessions, "最近使用的 64 个会话必须保留"
