"""REQ-1 覆盖盘点数据源（PRD §3.3 盘点＋§3.1 支持矩阵；数据驱动）。

当前状态：五族税项引擎均已实现并经官方示例与官方计算器实时对照核验
（docs/validation/001-engine-io-report.md、002-live-official-calculator-
comparison.md，最近对照 2026-10-13，9/9 一致）→ support_level=computable
且携带 calc_entry（计算页入口）。PRD §3.3 明确排除的其他应课税品以
unsupported 如实列出、不带计算入口（未实现项不得携带计算入口）。

verified_date＝最近一次官方对照核验日；后续引擎/核验状态变化须同步本表。
"""

from __future__ import annotations

_DIRECT_PERIODS = ["2024/25", "2025/26", "2026/27"]
_STAMP_PERIODS = ["2024-04-01..2026-10-07"]
_VERIFIED = "2026-10-13"  # 官方计算器实时对照 9/9 一致（commit 72eaaaf）


def coverage_items() -> list[dict]:
    """返回覆盖清单条目（REQ-1 规范八字段；computable 条目附 calc_entry）。"""
    return [
        {
            "tax": "薪俸税",
            "subtopic": "应课税入息、MPF 强制性供款扣除、免税额、两径择低与年度宽减",
            "support_level": "computable",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/tax/budget.htm",
            "verified_date": _VERIFIED,
            "applicable_periods": list(_DIRECT_PERIODS),
            "calc_entry": ["/salaries"],
            "gaps": "已实现并经官方示例核验（PAM39 等）；供养父母/子女等完整家庭免税额体系与夫妻合并评税选择属后续阶段；2026/27 为未生效年度（需官方数据确认后计算并明示）",
        },
        {
            "tax": "利得税",
            "subtopic": "法团／非法团两级制、合伙业务份额（资格须显式确认）",
            "support_level": "computable",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/tax/bus_pft.htm",
            "verified_date": _VERIFIED,
            "applicable_periods": list(_DIRECT_PERIODS),
            "calc_entry": ["/profits"],
            "gaps": "已实现（两级制三项资格须全部显式确认、合伙份额）；2026/27 为未生效年度（需官方数据确认后计算并明示）",
        },
        {
            "tax": "物业税",
            "subtopic": "NAV 口径与 15% 标准税率",
            "support_level": "computable",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/faq/pty.htm",
            "verified_date": _VERIFIED,
            "applicable_periods": list(_DIRECT_PERIODS),
            "calc_entry": ["/property"],
            "gaps": "已实现（NAV、15% 标准税率、暂缴；官方示例 pty.htm Q7 核验）；2026/27 为未生效年度（需官方数据确认后计算并明示）",
        },
        {
            "tax": "个人入息课税",
            "subtopic": "评税选择（personal assessment）：各评税方式并列比较，非第四个独立税种",
            "support_level": "computable",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/faq/pty.htm",
            "verified_date": _VERIFIED,
            "applicable_periods": list(_DIRECT_PERIODS),
            "calc_entry": ["/personal-assessment"],
            "gaps": "已实现（并列比较；官方示例 Q32/PAM37 核验）；比较输入为当前支持的事实集，家庭免税额深化属后续阶段",
        },
        {
            "tax": "印花税",
            "subtopic": "住宅／非住宅 AVD、香港股票普通转让、普通租约",
            "support_level": "computable",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.gov.hk/en/residents/taxes/stamp/stamp_duty_rates.htm",
            "verified_date": _VERIFIED,
            "applicable_periods": list(_STAMP_PERIODS),
            "calc_entry": ["/stamps/property", "/stamps/stock", "/stamps/lease"],
            "gaps": "已实现（三类普通文书；官方对照核验）；文书日期初始窗口 2024-04-01 至 2026-10-07；特殊文书、额外印花税等范围外（PRD §3.3）",
        },
        {
            "tax": "其他应课税品",
            "subtopic": "烟酒税款、博彩税、酒店房租税、飞机乘客离境税、遗产税等",
            "support_level": "unsupported",
            "authority": "香港特别行政区政府相关部门",
            "official_source": "https://www.gov.hk/en/residents/taxes/",
            "verified_date": "",
            "applicable_periods": [],
            "gaps": "PRD §3.3 明确排除于本阶段范围；不提供计算入口",
        },
    ]
