"""印花税-物业 AVD 规则数据区（M4 泳道维护；Annex D D1）。

数值来源（T1；Annex B 台账 1.9）:
- Table 8（2024-04-01–2025-02-25）：sd_pty_rates.pdf p3（2023-02-22 11:00 起
  表；PRD 窗口自 2024-04-01 沿用）；Ord 12/2025 修订前。
- Table 9（2025-02-26–2026-02-25）：sd_pty_rates.pdf p3；Ord 12/2025 s3/s4。
- 2026-02-26 起住宅（Scale 1/2）与非住宅（Scale 3）：Ord 3/2026 s1(2)/s13/s14。
- 相关转易契固定 $100：sd_pty_rates.pdf Note (ii)＋Cap.117 s.29D(2)(a)。

计税基数/取整语义（D1.2）: B＝max(consideration, value) 精确金额（1999-04-01
起不向上取整至 100）；区间下开上闭（Exceeds／Does not exceed）选行后仅对
税额 ceil 至 1 元；禁止取相邻公式 min／平滑／插值。

分档行格式: duty = fixed + rate_bp/10000 × (B − excess_over)；
  upto 缺省＝无上限（末行）；fixed 缺省 "0"；excess_over 缺省 "0"。
"""
from __future__ import annotations

DATA: dict = {
    # D1.1 日期制度（to=None＝开放至上窗末端）；transition_date＝该制度所依据
    # 修订条例生效日（s78=2025-02-26／s79(3)=2026-02-26；2024 窗无修订）。
    # 2026 窗住宅/非住宅分表 → requires_property_class=True（缺 → 追问）。
    "regimes": [
        {
            "from": "2024-04-01",
            "to": "2025-02-25",
            "requires_property_class": False,
            "tables": {"default": "table_8"},
        },
        {
            "from": "2025-02-26",
            "to": "2026-02-25",
            "requires_property_class": False,
            "transition_date": "2025-02-26",
            "tables": {"default": "table_9"},
        },
        {
            "from": "2026-02-26",
            "to": None,
            "requires_property_class": True,
            "transition_date": "2026-02-26",
            "tables": {
                "residential": "table_2026_residential",
                "nonresidential": "table_2026_nonresidential",
            },
        },
    ],
    # D1.5 s78/s79(3)：修订前条例按协议日期保留（早于该日的同双方同条款协议
    # 及其取代/符合链）；按 agreement_before 升序匹配。
    "transitional": {
        "pre_amendment_tables": [
            {"agreement_before": "2025-02-26", "table": "table_8"},
            {"agreement_before": "2026-02-26", "table": "table_9"},
        ],
    },
    # D1.7/Note (ii)：已 duly stamped 买卖协议后的相关转易契固定 $100
    # （Cap.117 s.29D(2)(a)）。
    "related_conveyance": {"fixed_duty": "100"},
    # D1.3 原文档值（表 A–D；区间下开上闭）。
    "tables": {
        # 表 A — Table 8（2024-04-01–2025-02-25）
        "table_8": [
            {"upto": "3000000", "fixed": "100"},
            {"upto": "3528240", "fixed": "100", "rate_bp": 1000, "excess_over": "3000000"},
            {"upto": "4500000", "rate_bp": 150},
            {"upto": "4935480", "fixed": "67500", "rate_bp": 1000, "excess_over": "4500000"},
            {"upto": "6000000", "rate_bp": 225},
            {"upto": "6642860", "fixed": "135000", "rate_bp": 1000, "excess_over": "6000000"},
            {"upto": "9000000", "rate_bp": 300},
            {"upto": "10080000", "fixed": "270000", "rate_bp": 1000, "excess_over": "9000000"},
            {"upto": "20000000", "rate_bp": 375},
            {"upto": "21739120", "fixed": "750000", "rate_bp": 1000, "excess_over": "20000000"},
            {"rate_bp": 425},
        ],
        # 表 B — Table 9（2025-02-26–2026-02-25）
        "table_9": [
            {"upto": "4000000", "fixed": "100"},
            {"upto": "4323780", "fixed": "100", "rate_bp": 2000, "excess_over": "4000000"},
            {"upto": "4500000", "rate_bp": 150},
            {"upto": "4935480", "fixed": "67500", "rate_bp": 1000, "excess_over": "4500000"},
            {"upto": "6000000", "rate_bp": 225},
            {"upto": "6642860", "fixed": "135000", "rate_bp": 1000, "excess_over": "6000000"},
            {"upto": "9000000", "rate_bp": 300},
            {"upto": "10080000", "fixed": "270000", "rate_bp": 1000, "excess_over": "9000000"},
            {"upto": "20000000", "rate_bp": 375},
            {"upto": "21739120", "fixed": "750000", "rate_bp": 1000, "excess_over": "20000000"},
            {"rate_bp": 425},
        ],
        # 表 C — 2026-02-26 起住宅（Table 9 至 21,739,120＋住宅高端档）
        "table_2026_residential": [
            {"upto": "4000000", "fixed": "100"},
            {"upto": "4323780", "fixed": "100", "rate_bp": 2000, "excess_over": "4000000"},
            {"upto": "4500000", "rate_bp": 150},
            {"upto": "4935480", "fixed": "67500", "rate_bp": 1000, "excess_over": "4500000"},
            {"upto": "6000000", "rate_bp": 225},
            {"upto": "6642860", "fixed": "135000", "rate_bp": 1000, "excess_over": "6000000"},
            {"upto": "9000000", "rate_bp": 300},
            {"upto": "10080000", "fixed": "270000", "rate_bp": 1000, "excess_over": "9000000"},
            {"upto": "20000000", "rate_bp": 375},
            {"upto": "21739120", "fixed": "750000", "rate_bp": 1000, "excess_over": "20000000"},
            {"upto": "100000000", "rate_bp": 425},
            {"upto": "109574470", "fixed": "4250000", "rate_bp": 3000, "excess_over": "100000000"},
            {"rate_bp": 650},
        ],
        # 表 D — 2026-02-26 起非住宅（Scale 3＝Table 9 全档，无高端档）
        "table_2026_nonresidential": [
            {"upto": "4000000", "fixed": "100"},
            {"upto": "4323780", "fixed": "100", "rate_bp": 2000, "excess_over": "4000000"},
            {"upto": "4500000", "rate_bp": 150},
            {"upto": "4935480", "fixed": "67500", "rate_bp": 1000, "excess_over": "4500000"},
            {"upto": "6000000", "rate_bp": 225},
            {"upto": "6642860", "fixed": "135000", "rate_bp": 1000, "excess_over": "6000000"},
            {"upto": "9000000", "rate_bp": 300},
            {"upto": "10080000", "fixed": "270000", "rate_bp": 1000, "excess_over": "9000000"},
            {"upto": "20000000", "rate_bp": 375},
            {"upto": "21739120", "fixed": "750000", "rate_bp": 1000, "excess_over": "20000000"},
            {"rate_bp": 425},
        ],
    },
}
