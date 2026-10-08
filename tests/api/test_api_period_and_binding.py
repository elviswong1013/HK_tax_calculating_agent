"""REQ-2 年度键 pin 已验证 bundle；超范围/键缺失拒算且不回退当前年度。

REQ: REQ-2（SDD §9：计算按年度键/文书日期 pin 已验证 bundle；响应带证据引用
与状态；2026/27 显示未完结提示；超范围/日期缺失/状态不可核实 → 拒算，
不回退当前年度）。
规格锚点:
  - SDD §5：RuleBundle 冻结 schema `rules_schema_version = 1.0.0`；直接税年度键
    2024_25/2025_26/2026_27（§1 支持矩阵）。
  - SDD §9 REQ-2 行；SDD §4 错误分类法（E_PERIOD_NOT_SUPPORTED 422；
    E_INPUT_MISSING 422）＋错误响应统一形状（field 指向触发字段）。
  - Annex C C2.11：课税年度键 `YYYY_YY`（如 2025_26）。
  - Annex C C3.2.1：prepare 201 返回完整四元组 expected_binding。
  - Annex C C3.1：GET /api/v1/meta/rules＝规则与来源健康。
期望值来源: 输入载荷取 SDD §3 草图事实（240000/9000 仅输入）；期望全部为
结构/状态断言（binding 四元组、schema 版本、拒算码）；无税额数值期望。

【拟名】契约说明:
  - pin 一致性经 /api/v1/meta/rules 交叉验证：prepare 所 pin 的 bundle_hash
    必须出现在当前已验证规则清单中（text 级断言，信封形状不预设）。
"""

from __future__ import annotations

import json
import re


def _salaries_body(year: str = "2025_26") -> dict:
    return {
        "tax_type": "salaries_tax",
        "schema_version": "1.0.0",
        "input": {
            "year_of_assessment": year,
            "employment_income": "240000",
            "mpf_mandatory_contributions": "9000",
            "married_status": {"value": "single"},
        },
    }


def test_year_pins_verified_bundle(api) -> None:
    """三个支持年度均 201 且 pin 指向同一已验证 bundle；binding 完整且确定。"""
    for year in ("2024_25", "2025_26", "2026_27"):
        r = api.post("/api/v1/calc/prepare", json=_salaries_body(year))
        assert r.status_code == 201, f"{year}: {r.text}"
        payload = r.json()
        assert payload["status"] == "prepared"
        binding = payload["expected_binding"]
        assert set(binding.keys()) == {
            "bundle_id", "bundle_hash", "rules_schema_version", "engine_version",
        }
        assert binding["rules_schema_version"] == "1.0.0"  # §5 冻结 schema
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", binding["bundle_hash"])

        # —— pin 一致性：同一输入重复 prepare，binding 完全一致（不漂移）——
        r_again = api.post("/api/v1/calc/prepare", json=_salaries_body(year))
        assert r_again.status_code == 201, r_again.text
        assert r_again.json()["expected_binding"] == binding

        # —— pin 的是已验证 bundle：bundle_hash 出现在规则/来源健康清单 ——
        rules = api.get("/api/v1/meta/rules")
        assert rules.status_code == 200, rules.text
        assert binding["bundle_hash"] in json.dumps(
            rules.json(), ensure_ascii=False
        ), f"{year} pin 的 bundle_hash 未见于 /api/v1/meta/rules"

    # —— 2026/27 未完结提示（REQ-2）——
    r2627 = api.post("/api/v1/calc/prepare", json=_salaries_body("2026_27"))
    assert r2627.status_code == 201, r2627.text
    assert "未完结" in json.dumps(r2627.json(), ensure_ascii=False), (
        "2026/27 为未完结年度，响应必须携带未完结提示（REQ-2）"
    )


def test_out_of_range_refused_no_current_fallback(api) -> None:
    """超范围年度/未来年度 → 422 E_PERIOD_NOT_SUPPORTED；年度键缺失 →
    422 E_INPUT_MISSING；一律不得以当前年度规则回放计算（无 201、无 binding）。"""
    for bad_year in ("2023_24", "2027_28"):
        r = api.post("/api/v1/calc/prepare", json=_salaries_body(bad_year))
        assert r.status_code == 422, f"{bad_year}: {r.text}"
        err = r.json()["error"]
        assert err["code"] == "E_PERIOD_NOT_SUPPORTED"
        assert err["field"] == "year_of_assessment"
        assert isinstance(err["message_zh"], str) and err["message_zh"].strip()
        # 不回退当前年度：拒绝路径不得出现绑定或计算产物
        assert "expected_binding" not in r.json()
        assert "prepared_id" not in r.json()

    # —— 年度键缺失（REQ-2 日期/期间缺失 → 拒算）——
    body = _salaries_body()
    del body["input"]["year_of_assessment"]
    r_missing = api.post("/api/v1/calc/prepare", json=body)
    assert r_missing.status_code == 422, r_missing.text
    assert r_missing.json()["error"]["code"] == "E_INPUT_MISSING"
    assert r_missing.json()["error"]["field"] == "year_of_assessment"
