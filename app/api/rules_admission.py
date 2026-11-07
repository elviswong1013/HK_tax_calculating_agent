# -*- coding: utf-8 -*-
"""规则束准入 API adapter（R1/Bu4；Annex 005 §2.1/§2.2、§15.1、§15.4；S002 §4.2）。

公共签名（bounded-plan §3 冻结兼容）：
`verify_bundle_admission(content, *, tax_type, year) -> None`（成功 None；
不可核验 → `E_RULES_STATE_UNVERIFIABLE` 422）。

本 adapter 只做签名转发：内容级 schema／固定 P＋digest／活动年度参数核验集中
于中立 `app.rules.admission`（引擎方向不反向依赖 API）。真实 context/hash
准入证据由调用方（routes prepare/execute 消费路径＋两纯引擎
`check_availability`）在同一中立权威上完成。
"""

from __future__ import annotations

from typing import Any

from app.rules.admission import verify_bundle_admission as _verify_bundle_admission


def verify_bundle_admission(
    content: Any, *, tax_type: str, year: str | None
) -> None:
    """七目标共同准入（任一失败 → E_RULES_STATE_UNVERIFIABLE，拒算）。"""
    return _verify_bundle_admission(content, tax_type=tax_type, year=year)


__all__ = ["verify_bundle_admission"]
