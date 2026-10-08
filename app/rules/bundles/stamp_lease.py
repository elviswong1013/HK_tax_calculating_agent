"""印花税-租约规则数据区（M4 泳道维护；Annex D D3）。

数值来源（T1）:
  - 档位费率与 boundary 扫描语义＝Annex D D3.1 表（Cap.117 head 1 sub-head (2)
    现行文本＋GovHK）＋D3.1a 官方租约计算器（SD_RATE_MIN_YEARS/SD_RATE 同构：
    非固定 → 0.25%；恰 1 年 → 0.25%；1 年 1 天–恰 3 年 → 0.5%；>3 年 → 1%）。
  - 「每 $100 或其部分」＝ceil100 法定基础（D3.1；税额另 ceil1）。
  - premium 含租费率＝D3.2（3 of 2026 s.14 现行 Note 1）：2024-04-01–2026-02-25
    → 4.25%（修订前口径）；2026-02-26 起住宅 6.5%／非住宅 4.25%。
    无租 premium → 按文书日期适用 AVD 表：物业泳道同表逻辑
    （app.engines.stamps.property.calculate_property_avd，不在此复制档表）。
  - 复本（HEAD 4）＝D3.3（T1）：原本税额 < $5 → 与原本同额；否则每份 $5。
"""

DATA: dict = {
    # 租金档位（周年日，Fraction 年数 > min_years 时取该档，自高向低扫描）。
    # 与官方计算器 SD_RATE_MIN_YEARS[0..3]={-1,0,1,3}／SD_RATE={.25,.25,.5,1}
    # 同构；非固定期限以 -0.5 参与比较（恒落 0.25% 档）。
    "rent_rate_tiers": [
        {"min_years": -1, "rate": "1/400"},  # 未界定/不确定：0.25%（年租/平均年租）
        {"min_years": 0, "rate": "1/400"},  # 不超过 1 年：0.25%（租期内总租金）
        {"min_years": 1, "rate": "1/200"},  # 超过 1 年但不超过 3 年：0.5%
        {"min_years": 3, "rate": "1/100"},  # 超过 3 年：1%
    ],
    # 租金基数向上取整单位（「每 $100 或其部分」，D3.1 法定基础）
    "rent_ceiling_unit": "100",
    "premium": {
        # 3 of 2026 s.14 切换日（现行 Note 1 住宅 6.5%／非住宅 4.25% 起算）
        "current_regime_from": "2026-02-26",
        # 2024-04-01–2026-02-25 含租 premium（修订前 Note 1 口径）
        "with_rent_rate_historical": "17/400",
        # 2026-02-26 起含租 premium：住宅 6.5%
        "with_rent_rate_residential": "13/200",
        # 2026-02-26 起含租 premium：非住宅 4.25%
        "with_rent_rate_nonresidential": "17/400",
        # premium>0 计税必需的物业类别（住宅/非住宅）
        "property_classes": ["residential", "nonresidential"],
    },
    "duplicate": {
        # HEAD 4：原本税额 ≥ $5 → 每份 $5
        "per_copy": "5",
        # HEAD 4 低额例外：原本税额 < $5 → 每份与原本同额
        "low_original_below": "5",
    },
}
