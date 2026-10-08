"""REQ-1 覆盖盘点数据源（PRD §3.3 盘点＋§3.1 支持矩阵；数据驱动）。

M1：全部税项引擎未实现 → support_level 一律 unimplemented、无 calc_entry
（未实现项不得携带计算入口）；verified_date 为空＝尚无已核验数值
（未晋升 T1 者不得进入规则数据，SDD §12 恒久注记）。
"""

from __future__ import annotations

_DIRECT_PERIODS = ["2024/25", "2025/26", "2026/27"]
_STAMP_PERIODS = ["2024-04-01..2026-10-07"]


def coverage_items() -> list[dict]:
    """返回覆盖清单条目（REQ-1 规范八字段）。"""
    return [
        {
            "tax": "薪俸税",
            "subtopic": "应课税入息、MPF 强制性供款扣除、免税额、两径择低与年度宽减",
            "support_level": "unimplemented",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/tax/budget.htm",
            "verified_date": "",
            "applicable_periods": list(_DIRECT_PERIODS),
            "gaps": "引擎未实现（M2 里程碑）；官方一手数值未晋升 T1，不进入规则数据",
        },
        {
            "tax": "利得税",
            "subtopic": "法团／非法团两级制、合伙业务份额（资格须显式确认）",
            "support_level": "unimplemented",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/tax/bus_pft.htm",
            "verified_date": "",
            "applicable_periods": list(_DIRECT_PERIODS),
            "gaps": "引擎未实现（M2 里程碑）",
        },
        {
            "tax": "物业税",
            "subtopic": "NAV 口径与 15% 标准税率",
            "support_level": "unimplemented",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/faq/pty.htm",
            "verified_date": "",
            "applicable_periods": list(_DIRECT_PERIODS),
            "gaps": "引擎未实现（M2 里程碑）",
        },
        {
            "tax": "个人入息课税",
            "subtopic": "评税选择（personal assessment）：各评税方式并列比较，非第四个独立税种",
            "support_level": "unimplemented",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/faq/pty.htm",
            "verified_date": "",
            "applicable_periods": list(_DIRECT_PERIODS),
            "gaps": "比较引擎未实现（M3 里程碑）",
        },
        {
            "tax": "印花税",
            "subtopic": "住宅／非住宅 AVD、香港股票普通转让、普通租约",
            "support_level": "unimplemented",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.gov.hk/en/residents/taxes/stamp/stamp_duty_rates.htm",
            "verified_date": "",
            "applicable_periods": list(_STAMP_PERIODS),
            "gaps": "引擎未实现（M4 里程碑）；文书日期初始窗 2024-04-01 至 2026-10-07",
        },
    ]
