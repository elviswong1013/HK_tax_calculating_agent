"""M1/M2 初始规则束（Annex C C2.8 封闭 6 字段）。

结构：`data` 按引擎分区装配自 app/rules/bundles/{salaries,profits,property,...}.py，
各引擎泳道独立维护本区数值（T1 台账晋升后方可入数；Annex B）。

- `effective`：已入数规则的生效期（年度/文书窗）＋台账锚点（C2.8/REQ-2）。
- `frozen_semantics.profiles`：各引擎取整/计算口径链，冻结进束（REQ-9）。
- `evidence_digests`：已入数数值的官方出处摘要——条目定形
  {tier, ledger_anchor, source_id, url, fetched_at, content_hash, covers}；
  tier 恒 "T1"（台账直接核实），ledger_anchor＝
  docs/research/001-hk-tax-rule-evidence.md §1 条目号；url/fetched_at/
  content_hash＝台账 §8 精确来源＋抓取日（2026-10-08）＋本地保存官方页面
  快照（.playwright-mcp/page-2026-10-08T*.yml）实算 sha256 指纹。
"""

from __future__ import annotations

from app.rules.bundles import personal_assessment as _pa
from app.rules.bundles import profits as _profits
from app.rules.bundles import property_ as _property
from app.rules.bundles import salaries as _salaries
from app.rules.bundles import stamp_lease as _stamp_lease
from app.rules.bundles import stamp_property as _stamp_property
from app.rules.bundles import stamp_stock as _stamp_stock

# 台账 §1 条目号（docs/research/001-hk-tax-rule-evidence.md）
_LEDGER_CAP112 = "1.17"  # T1-现行法例文本：Cap.112 附表 1/2/3B/3C/4/43 等
_LEDGER_CAP117 = "1.18"  # T1-现行法例文本：Cap.117 head 1/2/4、s.29D
_LEDGER_SDO_PDF = "1.9"  # T1：印花税修订条例与 Table 8/9 原税率 PDF
_LEDGER_SAL_CALC = "1.10"  # T1-observed：薪俸税估算器取整（三年度）
_LEDGER_PA_CALC = "1.11"  # T1-observed：PA 计算器取整与两级分支
_LEDGER_2TIER = "1.12"  # T1-observed：利得税两级制（2tr.htm）
_LEDGER_LEASE = "1.14"  # T1/T2：租约 IRSD119＋官方租约计算器
_LEDGER_PROPERTY = "1.15"  # T1/T2：物业税指南与 BIR57 Notes

_EVIDENCE_URL_CAP112 = "https://www.elegislation.gov.hk/hk/cap112!en"
_EVIDENCE_URL_CAP117 = "https://www.elegislation.gov.hk/hk/cap117!en"

# 已入数规则的生效期（C2.8 effective；与 applicability/各数据区年度键一致）
_EFFECTIVE: dict = {
    "salaries": {
        "progressive_bands": {"from_year": "2018_19", "ledger_anchor": _LEDGER_CAP112},
        "standard_rate_bands": {"from_year": "2024_25", "ledger_anchor": _LEDGER_CAP112},
        "mpf_deduction_cap": {"from_year": "2015_16", "ledger_anchor": _LEDGER_CAP112},
        "allowances": {
            "from_year": "2024_25",
            "to_year": "2026_27",
            "ledger_anchor": _LEDGER_CAP112,
        },
        "reductions": {
            "from_year": "2024_25",
            "to_year": "2025_26",
            "ledger_anchor": _LEDGER_CAP112,
        },
        "deductions": {
            "elder_residential_care": {
                "from_year": "2018_19",
                "to_year": "2026_27",
                "ledger_anchor": _LEDGER_CAP112,
            },
        },
    },
    "profits": {
        "two_tier": {"from_year": "2018_19", "ledger_anchor": _LEDGER_2TIER},
        "reductions": {
            "from_year": "2024_25",
            "to_year": "2025_26",
            "ledger_anchor": _LEDGER_CAP112,
        },
    },
    "property": {
        "standard_rate": {
            "from_year": "2020_21",
            "to_year": "2026_27",
            "ledger_anchor": _LEDGER_PROPERTY,
        },
        "repair_allowance_ratio": {
            "legal_basis": "s.5(1A)(b)(ii)",
            "ledger_anchor": _LEDGER_CAP112,
        },
    },
    "personal_assessment": {
        "progressive_bands": {"from_year": "2018_19", "ledger_anchor": _LEDGER_CAP112},
        "standard_rate_bands": {"from_year": "2024_25", "ledger_anchor": _LEDGER_CAP112},
        "mpf_deduction_cap": {"from_year": "2015_16", "ledger_anchor": _LEDGER_CAP112},
        "allowances": {
            "from_year": "2024_25",
            "to_year": "2026_27",
            "ledger_anchor": _LEDGER_CAP112,
        },
        "reductions": {
            "from_year": "2024_25",
            "to_year": "2025_26",
            "ledger_anchor": _LEDGER_CAP112,
        },
    },
    "stamps": {
        "property": {
            "table_8_from": "2024-04-01",
            "table_9_from": "2025-02-26",
            "scale_residential_nonresidential_from": "2026-02-26",
            "ledger_anchor": _LEDGER_SDO_PDF,
        },
        "stock": {
            "head2_effective_from": "2023-11-17",
            "ledger_anchor": _LEDGER_CAP117,
        },
        "lease": {
            "rate_tiers_window_from": "2024-04-01",
            "ledger_anchor": _LEDGER_LEASE,
        },
    },
}

# 取整/计算口径冻结（REQ-9：语义不随数据更新变化；profile 冻结进束）
_FROZEN_PROFILES: list[dict] = [
    {
        "profile_id": "salaries_two_path_floor",
        "engines": ["salaries_tax"],
        "rule": (
            "累进径与标准径先分别 floor 再比较、floor 后相等取累进、外层单次"
            " floor；宽减 floor(min(税×率, cap))；无级距内取整"
        ),
        "ledger_anchor": _LEDGER_SAL_CALC,
    },
    {
        "profile_id": "pa_calculator_floor_chain",
        "engines": ["personal_assessment"],
        "rule": (
            "CompTP 原始值严格 <、平局取累进、外层单次 floor；标准上限基数＝"
            "PA 减少后总入息；宽减 ceil(min(税×率, cap))；两级利得择档 "
            "floor(份额利润)≤2,000,000 → 低档全率、税额条件分支后 floor"
        ),
        "ledger_anchor": _LEDGER_PA_CALC,
    },
    {
        "profile_id": "profits_unincorporated_floor_chain",
        "engines": ["profits_tax"],
        "rule": (
            "非法团：择档 floor(净利)≤2,000,000 → 低档全率、税额 raw 后 floor、"
            "宽减 ceil 后扣；法团/混合＝产品精度约定 floor（结果带待验证标识）"
        ),
        "ledger_anchor": _LEDGER_PA_CALC,
    },
    {
        "profile_id": "property_per_unit_floor",
        "engines": ["property_tax"],
        "rule": (
            "评税单位＝逐项物业：NAV＝(AV−同意且已缴差饷)×(1−1/5) 先括号后 "
            "floor(NAV)→floor(NAV×15%)；附表 43 无物业税行、无宽减"
        ),
        "ledger_anchor": _LEDGER_PROPERTY,
    },
    {
        "profile_id": "avd_exact_base_ceil1",
        "engines": ["stamp_property"],
        "rule": (
            "B＝max(consideration, value) 精确金额（1999-04-01 起不向上取整至 "
            "100）；分档区间下开上闭、选行后仅税额 ceil 至 1 元"
        ),
        "ledger_anchor": _LEDGER_SDO_PDF,
    },
    {
        "profile_id": "stock_per_note_ceil1",
        "engines": ["stamp_stock"],
        "rule": (
            "每份 contract note 独立 ceil1 后相加（sold/bought 不合并）；"
            "consideration or value（非取较高者）"
        ),
        "ledger_anchor": _LEDGER_CAP117,
    },
    {
        "profile_id": "lease_ceil100_ceil1",
        "engines": ["stamp_lease"],
        "rule": (
            "基数「每 $100 或其部分」ceil100 → 乘率 → 税额 ceil1；premium 与 "
            "租金独立 ceil1；平均年租分支先 round4；按金不计入"
        ),
        "ledger_anchor": _LEDGER_LEASE,
    },
]

# 官方出处摘要（REQ-2/REQ-13；tier 恒 T1＝台账直接核实；入数数值全部 T1 晋升）
#
# 溯源五件（SDD §9 REQ-15 行＋Annex C C8.2）：url（台账 §8 精确来源）＋
# fetched_at（YYYY-MM-DD 抓取日）＋content_hash（sha256:<64hex>）＋tier＋
# ledger_anchor（台账 §1 条目号）。content_hash 为本地保存官方页面快照
# （.playwright-mcp/page-2026-10-08T*.yml，台账 §8 #29/#30 引用件）实算
# sha256——逐项可复核，不使用不可核验的「声称来自官方源」条目；无本地
# 可核验快照的来源（如 PDF/计算器观察件）不入本清单，其溯源由
# effective/frozen_semantics 的台账锚点承载。
_EVIDENCE_FETCHED_AT = "2026-10-08"
_EVIDENCE_HASH_CAP112 = (  # .playwright-mcp/page-2026-10-08T04-16-44-396Z.yml
    "sha256:0c08a1076820e30ab680b5e9277a87a3538c7aa137271e483fdc7f3a04be25e7"
)
_EVIDENCE_HASH_CAP117 = (  # .playwright-mcp/page-2026-10-08T04-10-43-739Z.yml
    "sha256:80e4bf604768662088e09c6257edfcc168c1148797eb2bd9f9906abc751dac09"
)


def _digest(
    ledger_anchor: str,
    source_id: str,
    url: str,
    content_hash: str,
    covers: str,
) -> dict:
    return {
        "tier": "T1",
        "ledger_anchor": ledger_anchor,
        "source_id": source_id,
        "url": url,
        "fetched_at": _EVIDENCE_FETCHED_AT,
        "content_hash": content_hash,
        "covers": covers,
    }


_EVIDENCE_DIGESTS: list[dict] = [
    _digest(
        _LEDGER_CAP112,
        "elegislation_cap112",
        _EVIDENCE_URL_CAP112,
        _EVIDENCE_HASH_CAP112,
        "salaries：附表 2 累进带（2018/19 起 2/6/10/14%、余额 17% 五段）",
    ),
    _digest(
        _LEDGER_CAP112,
        "elegislation_cap112",
        _EVIDENCE_URL_CAP112,
        _EVIDENCE_HASH_CAP112,
        "salaries：附表 1 两级标准税率（2024/25 起首 $5,000,000 15%、余额 16%）",
    ),
    _digest(
        _LEDGER_CAP112,
        "elegislation_cap112",
        _EVIDENCE_URL_CAP112,
        _EVIDENCE_HASH_CAP112,
        "salaries：附表 3B MPF/RORS 强制性供款扣除上限（每人 $18,000）",
    ),
    _digest(
        _LEDGER_CAP112,
        "elegislation_cap112",
        _EVIDENCE_URL_CAP112,
        _EVIDENCE_HASH_CAP112,
        (
            "salaries：附表 4 免税额矩阵（基本/已婚/子女含出生年与次年额外、"
            "受养父母 60+/55–59 档；2024/25–2026/27；s.30/30A/31）"
        ),
    ),
    _digest(
        _LEDGER_CAP112,
        "elegislation_cap112",
        _EVIDENCE_URL_CAP112,
        _EVIDENCE_HASH_CAP112,
        "salaries：附表 43 宽减表（2024/25 100%/$1,500、2025/26 100%/$3,000、2026/27 无条目）",
    ),
    _digest(
        _LEDGER_CAP112,
        "elegislation_cap112",
        _EVIDENCE_URL_CAP112,
        _EVIDENCE_HASH_CAP112,
        "salaries：附表 3C 长者住宿照顾开支扣除上限（2026/27 起 $110,000）",
    ),
    _digest(
        _LEDGER_CAP112,
        "elegislation_cap112",
        _EVIDENCE_URL_CAP112,
        _EVIDENCE_HASH_CAP112,
        "property：s.5(1A) NAV 口径（差饷两条件、1/5 修葺及支出免税额）与标准税率 15%",
    ),
    _digest(
        _LEDGER_CAP112,
        "elegislation_cap112",
        _EVIDENCE_URL_CAP112,
        _EVIDENCE_HASH_CAP112,
        (
            "personal_assessment：s.41–43/s.100＋附表 1/2/4/43（PA 减少后总入息、"
            "标准上限、Part 5 免税额、宽减与分摊）"
        ),
    ),
    _digest(
        _LEDGER_CAP112,
        "elegislation_cap112",
        _EVIDENCE_URL_CAP112,
        _EVIDENCE_HASH_CAP112,
        "profits：附表 43 利得税宽减表（2024/25 100%/$1,500、2025/26 100%/$3,000）",
    ),
    _digest(
        _LEDGER_CAP117,
        "elegislation_cap117",
        _EVIDENCE_URL_CAP117,
        _EVIDENCE_HASH_CAP117,
        (
            "stamps.stock／stamps.lease：head 2 股票（0.1%、$1 分数计作 $1）、"
            "head 1(2) 租约档位与「每 $100 或其部分」、head 4 复本、s.10(4) 多项代价"
        ),
    ),
]

INITIAL_BUNDLE_CONTENT: dict = {
    "rules_schema_version": "1.0.0",
    "effective": _EFFECTIVE,
    "applicability": {
        "years": ["2024_25", "2025_26", "2026_27"],
        "stamp_duty_instrument_window": {"from": "2024-04-01", "to": "2026-10-07"},
    },
    "frozen_semantics": {"profiles": _FROZEN_PROFILES},
    "data": {
        "salaries": _salaries.DATA,
        "profits": _profits.DATA,
        "property": _property.DATA,
        "personal_assessment": _pa.DATA,
        "stamps": {
            "property": _stamp_property.DATA,
            "stock": _stamp_stock.DATA,
            "lease": _stamp_lease.DATA,
        },
    },
    "evidence_digests": _EVIDENCE_DIGESTS,
}
