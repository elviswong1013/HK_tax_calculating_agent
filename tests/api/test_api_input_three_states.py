"""REQ-10 三态输入（缺失≠显式零≠unknown）＋类型严格（额外字段/超限）。

REQ: REQ-10（SDD §9：三态输入；类型严格（布尔/非有限/额外字段/超限拒绝）；
矛盾资料结构化拒算；拒算响应含中文问题清单）。
规格锚点:
  - SDD §3：输入三态——缺失（键不存在）≠显式零（合法）≠unknown（仅资格类字段
    允许，触发追问）；聚合字段禁止静默零默认。
  - Annex C C3.2.1 prepare：仅事实校验 → 201 `{prepared_id, input_revision,
    input_hash, expected_binding(完整四元组), preview, expires_in=300,
    status:"prepared"}`；needs_input/blocked → 422＋questions[]/blocked_reason，
    不产生任何 id。
  - Annex C C3.5 公共字段：`extra="forbid"`；薪俸税族字段
    `year_of_assessment`／`employment_income`／`mpf_mandatory_contributions`／
    `married_status{value}`／`dependent_children[]{birth_date,residence}`。
  - Annex C C2.9：载荷 ≤1MB；`rank` 不是权威输入——提交 rank 按 E_INPUT_EXTRA
    拒绝。
  - Annex C C3.7：E_INPUT_MISSING／E_INPUT_TYPE／E_INPUT_EXTRA → 422；
    E_INPUT_OVERSIZE → 413；E_ELIGIBILITY_UNKNOWN → 422＋questions[]。
  - SDD §4 错误响应统一形状：{"error":{code, message_zh, field, details, binding}}。
期望值来源: 输入示例取 SDD §3 输入模型草图（PAM39 Q1 T2 候选的输入事实
240000/9000——仅作输入载荷，不作任何税额期望）；全部期望为结构/状态断言。

【拟名】契约说明:
  - married_status.value 枚举含 "single"（草图仅权威示例 "unknown"）；
  - 列表字段（dependent_children 等）缺失＝无申索（非静默零默认）。
"""

from __future__ import annotations

import json
import re


def _salaries_input(**overrides) -> dict:
    """完整、合法的薪俸税事实输入；overrides 可删除/覆盖键以构造三态用例。"""
    inp = {
        "year_of_assessment": "2025_26",
        "employment_income": "240000",
        "mpf_mandatory_contributions": "9000",
        "married_status": {"value": "single"},
    }
    for key, value in overrides.items():
        if value is ...:  # 哨兵：删除键
            inp.pop(key, None)
        else:
            inp[key] = value
    return inp


def _prepare_body(inp: dict) -> dict:
    return {"tax_type": "salaries_tax", "schema_version": "1.0.0", "input": inp}


def _error_of(payload: dict) -> dict:
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状 §4，实际：{payload!r}"
    return err


def test_missing_not_zero(api) -> None:
    """键缺失（≠显式零）→ 422 E_INPUT_MISSING 字段级拒算；不得静默按 0 计算。"""
    r = api.post("/api/v1/calc/prepare", json=_prepare_body(
        _salaries_input(mpf_mandatory_contributions=...)
    ))
    assert r.status_code == 422, r.text
    err = _error_of(r.json())
    assert err["code"] == "E_INPUT_MISSING"
    assert err["field"] == "mpf_mandatory_contributions"  # §4 字段级错误
    assert isinstance(err.get("message_zh"), str) and err["message_zh"].strip()
    # 拒算路径不得产生任何可执行 id 或绑定（C3.2.1）
    payload = r.json()
    assert "prepared_id" not in payload
    assert "expected_binding" not in payload


def test_explicit_zero_accepted(api) -> None:
    """显式 "0" 是合法事实 → 201 prepared＋完整四元组 binding＋TTL 300。"""
    r = api.post("/api/v1/calc/prepare", json=_prepare_body(
        _salaries_input(mpf_mandatory_contributions="0")
    ))
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["status"] == "prepared"
    assert payload["prepared_id"]
    assert payload["input_hash"].startswith("sha256:")
    binding = payload["expected_binding"]
    # C3.2.1：prepare 返回完整四元组（恰四键）
    assert set(binding.keys()) == {
        "bundle_id", "bundle_hash", "rules_schema_version", "engine_version",
    }
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", binding["bundle_hash"])
    assert binding["engine_version"]
    assert payload["expires_in"] == 300


def test_unknown_triggers_question_list(api) -> None:
    """资格类字段 value="unknown" → 422 E_ELIGIBILITY_UNKNOWN＋结构化中文问题清单。"""
    r = api.post("/api/v1/calc/prepare", json=_prepare_body(
        _salaries_input(married_status={"value": "unknown"})
    ))
    assert r.status_code == 422, r.text
    err = _error_of(r.json())
    assert err["code"] == "E_ELIGIBILITY_UNKNOWN"
    # 结构化中文问题清单（REQ-10：拒算响应含中文问题清单）
    questions = list(_find_questions(r.json()))
    assert questions, f"响应须含非空 questions[]，实际：{r.text!r}"
    for q in questions:
        assert isinstance(q, (str, dict))
    assert any(
        re.search(r"[\u4e00-\u9fff]", json.dumps(q, ensure_ascii=False))
        for q in questions
    ), "问题清单必须为中文"
    assert "prepared_id" not in r.json()  # 不产生任何 id（C3.2.1）


def test_extra_oversize_rejected(api) -> None:
    """额外字段（含 rank）→ 422 E_INPUT_EXTRA；载荷 >1MB → 413 E_INPUT_OVERSIZE。"""
    # —— 子女条目提交 rank：服务器派生字段，按 E_INPUT_EXTRA 拒绝（C2.9）——
    inp = _salaries_input()
    inp["dependent_children"] = [
        {"birth_date": "2024-05-01", "residence": {"value": True}, "rank": 1}
    ]
    r = api.post("/api/v1/calc/prepare", json=_prepare_body(inp))
    assert r.status_code == 422, r.text
    err = _error_of(r.json())
    assert err["code"] == "E_INPUT_EXTRA"
    assert err["field"] == "rank"

    # —— 顶层未知额外字段（extra="forbid"，C3.5）——
    r2 = api.post("/api/v1/calc/prepare", json=_prepare_body(
        _salaries_input(unexpected_field="x")
    ))
    assert r2.status_code == 422, r2.text
    err2 = _error_of(r2.json())
    assert err2["code"] == "E_INPUT_EXTRA"
    assert err2["field"] == "unexpected_field"

    # —— 载荷 >1MB → 413（C2.9；须在字段解析前判定，不得落为 422）——
    big = _salaries_input(pad="x" * (1024 * 1024))
    r3 = api.post("/api/v1/calc/prepare", json=_prepare_body(big))
    assert r3.status_code == 413, (
        f">1MB 载荷须 413 E_INPUT_OVERSIZE（C2.9），实际 {r3.status_code}：{r3.text[:200]!r}"
    )
    err3 = _error_of(r3.json())
    assert err3["code"] == "E_INPUT_OVERSIZE"


def _find_questions(node):
    """在响应 JSON 任意层级收集 questions[] 值。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "questions" and isinstance(value, list) and value:
                yield from value
            yield from _find_questions(value)
    elif isinstance(node, list):
        for item in node:
            yield from _find_questions(item)
