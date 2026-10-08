"""印花税-股票转让规则数据区（M4 stock 泳道维护；Annex D D2）。

数值来源（T1-现行法例文本 Cap.117 First Schedule head 2，2026-10-08 台账
1.9／Annex D D2.1；head 2 现行文本自 2023-11-17 起）:
  - Contract note（s.19(1) 须制作的每份 sold／bought note）：0.1% of the
    amount of the consideration or of its value；每份分别 ceil1（不足 $1
    向上取整至 $1），sold／bought 不合并、不互摊（D2.2）；
  - Voluntary disposition inter vivos／非买卖实益转让：$5＋0.2% of the
    value of the stock（按价值口径，与 contract note 措辞不同）；每份文书
    一次 ceil1；
  - 其他转让：$5 每份（不得替代一般买卖从价税、不得自动叠加于
    contract note——D2.2）；
  - 33 of 2024 s.7 废除原 (2) 项；豁免指针 Sch 8/9/10/11A＝范围外。
"""
from __future__ import annotations

DATA: dict = {
    # Head 2 现行文本生效日（D2.1）；更早期间的规则为 OPEN（D2.3），不猜。
    "effective_from": "2023-11-17",
    # Contract note（s.19(1)）：0.1% of the consideration or of its value
    # （货币代价按代价额、非货币按价值；**不是 max**——D2.2）。
    "contract_note_rate": "1/1000",
    # Voluntary disposition inter vivos：$5＋0.2% of the value of the stock。
    "voluntary_inter_vivos_fixed": "5",
    "voluntary_inter_vivos_rate": "1/500",
    # 其他转让：$5 每份。
    "other_transfer_fixed": "5",
}
