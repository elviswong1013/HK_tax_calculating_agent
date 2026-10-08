"""REQ-2/9/13/16 初始规则束证据完备性与 README 完整性（终验 REJECTED 缺口 Red）。

REQ: REQ-2（规则束结构：effective／applicability／冻结语义）／REQ-9
（取整与计算口径冻结进束）／REQ-13（record 官方出处 evidence_refs）／
REQ-16（运行说明完整）。
验收发现锚点（终验 REJECTED）:
  - app/rules/bundle.INITIAL_BUNDLE_CONTENT：effective={}、
    frozen_semantics.profiles=[]、evidence_digests=[] 全空——已入数规则
    （附表 1/2/3B/4/43 等）未挂任何生效期/取整 profile/T1 台账锚点证据；
    计算报告 evidence_refs 恒空（core 直读空 evidence_digests）。
  - README.md：缺 HKTAX_MODEL_ENDPOINT／HKTAX_MODEL_NAME（模型 env 变量名
    不全，app/api/consent.py 已实现三变量）；「依赖与许可」无逐依赖许可
    清单（仅一句「以 pyproject.toml 为准」）；维护章节缺「首启即查」声明
    （与 app/updater/scheduler.startup 实现不一致）。
规格锚点:
  - Annex C C2.8（RuleBundleContent 封闭 6 字段：rules_schema_version/
    effective/applicability/frozen_semantics/data/evidence_digests）；
    REQ-13 记录含官方出处。
  - 台账 T1 锚点＝docs/research/001-hk-tax-rule-evidence.md §1（如 1.17
    附表 1/2/3B/4/43）；入数数值全部 T1 晋升（Annex B）。
  - Annex C C4.2：HKTAX_MODEL_API_KEY（密钥）＋HKTAX_MODEL_ENDPOINT／
    HKTAX_MODEL_NAME（非秘密 env）。
  - REQ-17：首启即查（startup_initial）；SDD §8 维护说明与实现一致。
期望值来源: 结构/文本断言（非空、键形状、README 关键词）；无金额数值期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - evidence_digests 条目为结构化映射，至少含 tier=="T1" 与非空
    ledger_anchor（台账 §1 条目号，如 "1.17"）——与台账 T1 锚点一致。
"""

from __future__ import annotations

import re
from pathlib import Path

from _acc_helpers import confirm, execute, prepare, salaries_min_facts
from _acc_helpers import TAX_EXECUTE_TARGETS as _TARGETS

README = Path(__file__).resolve().parents[2] / "README.md"

# pyproject.toml [project].dependencies 的运行时依赖（许可清单须逐依赖覆盖）
RUNTIME_DEPS = ("fastapi", "jinja2", "pydantic", "uvicorn")
LICENSE_RE = re.compile(r"MIT|BSD|Apache|ISC|MPL|GPL", re.IGNORECASE)


def test_acceptance_bundle_evidence_and_profiles_populated(make_acc) -> None:
    """实际发布的 bundle：effective／applicability／rounding profiles／
    evidence_digests 非空且 evidence 挂 T1 台账锚点；计算报告
    evidence_refs 非空（REQ-13 官方出处）。"""
    acc = make_acc("bundle")
    bundle = acc.store.get_bundle(acc.store.current()["bundle_id"])

    # —— effective：已入数规则的生效期不得为空（C2.8／REQ-2）——
    effective = bundle.get("effective")
    assert isinstance(effective, dict) and effective, (
        "REQ-2：初始束 effective 不得为空映射——已入数规则须携带生效期，"
        f"实际 {effective!r}"
    )

    # —— applicability：支持年度齐备（对照断言；现状已满足）——
    years = (bundle.get("applicability") or {}).get("years") or []
    assert {"2024_25", "2025_26", "2026_27"} <= set(years), (
        f"REQ-2：applicability.years 须覆盖三个支持年度，实际 {years!r}"
    )

    # —— rounding profiles：取整/冻结语义不得为空（REQ-9）——
    profiles = (bundle.get("frozen_semantics") or {}).get("profiles") or []
    assert profiles, (
        "REQ-9：frozen_semantics.profiles 不得为空——各引擎取整链（两径 floor／"
        "ceil1／ceil100 等）须以 profile 冻结进束，实际 []"
    )
    assert all(
        isinstance(profile, dict) and profile for profile in profiles
    ), f"每个 profile 须为非空结构化映射：{profiles!r}"

    # —— evidence_digests：非空且与台账 T1 锚点一致（REQ-2/13）——
    digests = bundle.get("evidence_digests") or []
    assert digests, (
        "REQ-2/13：初始束 evidence_digests 不得为空——已入数规则数值须挂官方"
        "出处（台账 T1 晋升），实际 []"
    )
    for entry in digests:
        assert isinstance(entry, dict) and entry, (
            f"evidence 条目须为结构化映射：{entry!r}"
        )
        assert entry.get("tier") == "T1", (
            f"初始束数值证据须为 T1（台账直接核实）：{entry!r}"
        )
        anchor = entry.get("ledger_anchor")
        assert isinstance(anchor, str) and anchor.strip(), (
            f"evidence 条目须携带台账锚点 ledger_anchor（如 '1.17'）：{entry!r}"
        )

    # —— 报告 evidence_refs 非空（REQ-13：记录官方出处直读束证据）——
    r_prepare = prepare(acc.api, "salaries_tax", salaries_min_facts())
    assert r_prepare.status_code == 201, (
        f"prepare 须 201（对照 salaries 最小事实），实际 {r_prepare.status_code}: "
        f"{r_prepare.text[:200]}"
    )
    r_confirm = confirm(acc.api, r_prepare.json())
    assert r_confirm.status_code == 200, r_confirm.text[:200]
    r_execute = execute(
        acc.api, r_confirm.json()["confirmation_id"], _TARGETS["salaries_tax"]
    )
    assert r_execute.status_code == 200, (
        f"execute 须 200，实际 {r_execute.status_code}: {r_execute.text[:200]}"
    )
    evidence_refs = r_execute.json().get("evidence_refs") or []
    assert evidence_refs, (
        "REQ-13：计算报告 evidence_refs 不得为空（须直读规则束官方出处；"
        "当前初始束 evidence_digests 为空导致报告无出处）"
    )


def test_acceptance_readme_complete() -> None:
    """README 覆盖：模型 env 变量名三件套（密钥＋endpoint＋model）、逐依赖
    许可清单、按月更新声明与实现一致（按月＋首启即查＋手动检查）。"""
    assert README.exists(), "README.md 缺失（REQ-16）"
    text = README.read_text(encoding="utf-8")
    lines = text.splitlines()
    missing: list[str] = []

    # —— 模型 env 变量名（C4.2：密钥＋两个非秘密 env；consent.py 三变量）——
    for env_name in ("HKTAX_MODEL_API_KEY", "HKTAX_MODEL_ENDPOINT", "HKTAX_MODEL_NAME"):
        if env_name not in text:
            missing.append(f"模型 env 变量名 {env_name} 未写入 README（C4.2）")

    # —— 依赖许可清单：每个运行时依赖逐项标注许可 ——
    for dep in RUNTIME_DEPS:
        covered = any(
            dep.lower() in line.lower() and LICENSE_RE.search(line)
            for line in lines
        )
        if not covered:
            missing.append(
                f"依赖 {dep} 未列入许可清单（须逐依赖标注 MIT/BSD/Apache 等）"
            )

    # —— 按月更新声明与实现一致（REQ-17：首启即查＋按月＋手动同一管线）——
    monthly_ok = ("按月" in text) or ("每月" in text)
    first_startup_ok = any(
        marker in text for marker in ("首启", "首次启动", "启动即查", "启动后立即")
    )
    manual_ok = "手动" in text
    if not monthly_ok:
        missing.append("缺「按月/每月」更新节奏声明（与调度实现不一致）")
    if not first_startup_ok:
        missing.append(
            "缺「首启即查」声明（app/updater/scheduler.startup 首次启动立即检查；"
            "REQ-17）"
        )
    if not manual_ok:
        missing.append("缺「手动检查」声明（手动与计划同一管线，REQ-17）")

    assert not missing, (
        "README 验收缺口（REQ-16）：\n  - " + "\n  - ".join(missing)
    )
