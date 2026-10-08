"""物业税规则数据区（M2 property 泳道维护；T1 台账晋升后入数）。

模块名用 property_ 避免与内建/关键字混淆的导入歧义。

数据来源（T1-现行法例文本／官方资料，2026-10-08 台账）:
  - 标准税率 15%（2020/21–2026/27；BIR57 Note 3(c)＋附表 1「其他税项」）＝T1；
  - 20% 法定修葺及支出免税额基数为（AV−同意并已缴差饷）；比例以精确有理
    比率存之（s.5(1A)(b)(ii)，T1）；
  - 一次性宽减：附表 43 无物业税行（宽减对象仅薪俸/利得/PA）→ 各年度
    reduction 恒 "0"（T1；不套用他税宽减）。
"""
from __future__ import annotations

DATA: dict = {
    "years": {
        "2024_25": {"standard_rate": "15/100", "reduction": "0"},
        "2025_26": {"standard_rate": "15/100", "reduction": "0"},
        "2026_27": {"standard_rate": "15/100", "reduction": "0"},
    },
    # s.5(1A)(b)(ii)：NAV＝（AV−差饷）×（1−1/5）→ 20% 修葺及支出免税额
    "repair_allowance_ratio": "1/5",
}
