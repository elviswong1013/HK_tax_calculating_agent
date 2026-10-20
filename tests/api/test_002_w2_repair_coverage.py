"""P2 W2 repair — coverage 有限映射与保守 fallback（005:930）。

Audit (ora-56) 项 5：phase1/computable 须说明「仍须规则可用、资料及资格确认」；
needs_input/manual_handoff/phase2-3/unimplemented 须有确定标签；未知/缺失条目
须给保守说明与 /coverage 链接；不得新增 NLP/端点/外发键。
"""

from __future__ import annotations

import json

from app.web.coverage_data import chat_coverage_guidance, coverage_items


def _labels(item: dict) -> list[str]:
    rows = chat_coverage_guidance([item])["items"]
    assert len(rows) == 1
    return rows[0]["labels_zh"]


def _text(item: dict) -> str:
    return json.dumps(chat_coverage_guidance([item]), ensure_ascii=False)


def test_coverage_computable_phase1_still_requires_rules_and_confirmation() -> None:
    labels = _labels(
        {
            "tax": "薪俸税",
            "subtopic": "受雇入息",
            "support_level": "computable",
            "phase": 1,
            "limits_zh": "阶段 2 另行审批",
        }
    )
    text = "".join(labels)
    assert "可计算" in text
    assert "仍须规则可用" in text and "资料及资格确认" in text, labels


def test_coverage_needs_input_and_manual_handoff_labels() -> None:
    needs = _labels(
        {
            "tax": "利得税",
            "subtopic": "两级制",
            "support_level": "needs_input",
            "phase": 1,
            "limits_zh": "资格须显式确认",
        }
    )
    assert any("缺资料" in label for label in needs), needs
    assert any("未知不是否或零" in label for label in needs), needs

    manual = _labels(
        {
            "tax": "物业税",
            "subtopic": "NAV",
            "support_level": "manual_handoff",
            "phase": 1,
            "limits_zh": "边界-人工核实-本阶段范围外",
        }
    )
    assert any("人工" in label and "范围外" in label for label in manual), manual
    assert "边界-人工核实-本阶段范围外" in _text(
        {
            "tax": "物业税",
            "subtopic": "NAV",
            "support_level": "manual_handoff",
            "phase": 1,
            "limits_zh": "边界-人工核实-本阶段范围外",
        }
    )


def test_coverage_phase2_3_and_unimplemented_not_supported() -> None:
    phase2 = _labels(
        {
            "tax": "印花税",
            "subtopic": "其他算法",
            "support_level": "manual_handoff",
            "phase": 2,
            "limits_zh": "后续阶段，另行审批",
        }
    )
    text = "".join(phase2)
    assert "后续阶段" in text and "当前不支持计算" in text, phase2

    legacy = _labels(
        {
            "tax": "差饷",
            "subtopic": "未实现类别",
            "support_level": "unimplemented",
            "phase": 1,
            "limits_zh": "当前不支持计算",
        }
    )
    assert any("当前不支持计算" in label for label in legacy), legacy


def test_coverage_unknown_or_missing_items_get_conservative_fallback() -> None:
    cases = [
        {},
        {"tax": "未知税项"},
        {
            "tax": "未知税项",
            "support_level": "not-a-level",
            "phase": 1,
            "limits_zh": "",
        },
        {
            "tax": "未知税项",
            "support_level": "computable",
            "phase": "not-a-phase",
            "limits_zh": "",
        },
    ]
    for item in cases:
        labels = _labels(item)
        text = "".join(labels)
        assert labels, f"未知/缺失条目不得返回空 labels：{item!r}"
        assert "支持范围尚未确认" in text, (item, labels)
        assert "不作可计算承诺" in text, (item, labels)
        assert "/coverage" in text, (item, labels)


def test_coverage_production_items_all_have_nonempty_guidance() -> None:
    out = chat_coverage_guidance(coverage_items())
    assert len(out["items"]) == 5
    for row in out["items"]:
        assert row["labels_zh"], row
        assert not any(
            label == "支持范围尚未确认；不作可计算承诺" for label in row["labels_zh"]
        ), row
