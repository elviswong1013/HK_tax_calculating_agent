"""利得税规则数据区（M2 profits 泳道维护；T1 台账晋升后入数）。

数值来源（Annex B 台账；语义＝Annex D D4）:
- 台账 1.12（T1-observed：2tr.htm；D4.1）：法团 8.25%/16.5%、非法团 7.5%/15%，
  两级门槛 2,000,000（2018/19 起）。
- 台账 1.17（T1；附表 43 法定宽减全表）：2024/25 100%/$1,500；2025/26
  100%/$3,000；2026/27 无条目（当年无宽减）。
- D4.3 取整轮廓：非法团＝三年度官方计算器 floor 链（T1-observed，已闭）；
  法团/混合＝产品精度约定（未经 IRD 文字证明，结果须带「产品约定」标识，
  相反官方证据 → fail-closed）。
"""
from __future__ import annotations

DATA: dict = {
    # D4.1：两级制（2018/19 起）——首个 2,000,000 适用低档、其余高档。
    # 税率以 bp（万分之一）整数存储：825 bp = 8.25%。
    "two_tier_threshold": "2000000",
    "two_tier_rates_bp": {
        "corporation": {"low": 825, "high": 1650},  # 8.25% / 16.5%
        "unincorporated": {"low": 750, "high": 1500},  # 7.5% / 15%
    },
    # 附表 43（s.100(2)/(3)/(6)）：按课税年度条目；缺项＝该年度无宽减（2026/27）。
    "reductions": {
        "2024_25": {"percent_bp": 10000, "cap": "1500"},
        "2025_26": {"percent_bp": 10000, "cap": "3000"},
    },
    # D4.3 取整轮廓标识（引擎据此决定是否携带「产品约定」待验证项）。
    "rounding": {
        "unincorporated": "official_calculator_floor_chain",
        "corporation": "product_convention_floor",
        "mixed_partnership": "product_convention_floor",
    },
}
