"""REQ-12/14 回归：record_id 类型校验（replay/print 不得因类型错误 500）。

安全审查 F-13（2026-11-05，hktax-security-review-20261105）：非字符串
record_id（dict/list 等不可哈希值）曾触发未捕获 TypeError → 500。

【拟名】契约（安全修复回归，Green 不得要求改写）:
  - POST /api/v1/records/replay 与 /api/v1/report/print：record_id 存在
    但非字符串 → 422 E_INPUT_TYPE（统一错误形状，field=record_id）；
  - record_id 为字符串但未知、或缺失 → 维持 404（record not found）。
"""

from __future__ import annotations


def test_replay_rejects_non_string_record_id(api) -> None:
    for bad in ({"x": 1}, [1, 2], 123):
        r = api.post("/api/v1/records/replay", json={"record_id": bad})
        assert r.status_code == 422, (
            f"非字符串 record_id={bad!r} 应 422 E_INPUT_TYPE，"
            f"实际 {r.status_code}: {r.text[:200]!r}"
        )
        payload = r.json()["error"]
        assert payload["code"] == "E_INPUT_TYPE"
        assert payload["field"] == "record_id"


def test_print_rejects_non_string_record_id(api) -> None:
    for bad in ({"x": 1}, [1], 99):
        r = api.post("/api/v1/report/print", json={"record_id": bad})
        assert r.status_code == 422, (
            f"非字符串 record_id={bad!r} 应 422 E_INPUT_TYPE，"
            f"实际 {r.status_code}: {r.text[:200]!r}"
        )
        payload = r.json()["error"]
        assert payload["code"] == "E_INPUT_TYPE"
        assert payload["field"] == "record_id"


def test_unknown_or_missing_record_id_stays_404(api) -> None:
    unknown = api.post("/api/v1/records/replay", json={"record_id": "r-not-exist"})
    missing_replay = api.post("/api/v1/records/replay", json={})
    missing_print = api.post("/api/v1/report/print", json={})
    for r in (unknown, missing_replay, missing_print):
        assert r.status_code == 404, (
            f"未知/缺失 record_id 维持 404，实际 {r.status_code}: {r.text[:200]!r}"
        )
