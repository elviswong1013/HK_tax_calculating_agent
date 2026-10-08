"""REQ-16 运行说明（README）与不宣称 IRD 认证。

REQ: REQ-16（SDD §9：运行说明（安装/启动/模型配置与禁用/支持期间/维护/免责）；
不宣称 IRD 认证）。
规格锚点:
  - SDD §8 未来命令（获批解释器与启动约束）：
      `py -3.13 -m venv .venv`／`.venv\\Scripts\\python --version` 版本检查；
      `.venv\\Scripts\\pip install -e ".[dev]"`；
      `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
      --no-proxy-headers`（非回环绑定拒绝启动；启动器校验单 worker）。
  - Annex C C1（平台与依赖基线：test-only 先、production 后）。
  - Annex C C4.2（密钥仅 env HKTAX_MODEL_API_KEY；endpoint/model 为非秘密 env；
    无浏览器端密钥表单）→「模型配置与禁用」章节。
  - SDD §1 支持期间（直接税 2024/25–2026/27；印花税文书日期初始窗）。
期望值来源: 文档存在性＋关键词断言（REQ-16 语义）；无金额数值期望。
Red 说明: 仓库尚无 README.md —— Red 失败原因＝README.md 缺失断言
（assert README.exists()，附 REQ-16 说明；Green 创建 README.md 后转 Green）。
"""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"

# 不允许出现的「IRD 认可/认证」正向宣称（负向声明如「不获税务局认证」
# 以否定前缀豁免，见 _is_negated）
ENDORSEMENT_PATTERNS = [
    "税务局认证",
    "稅務局認證",
    "税务局认可",
    "稅務局認可",
    "IRD 认证",
    "IRD认证",
    "IRD 認證",
    "IRD認證",
    "IRD endorsed",
    "IRD certified",
    "endorsed by the IRD",
    "certified by the IRD",
    "officially endorsed",
    "税务局官方",
    "稅務局官方",
]
# 必须存在的非背书声明（至少其一）
NON_ENDORSEMENT_MARKERS = [
    "不宣称",
    "并非官方",
    "非官方",
    "不获税务局",
    "不隶属",
    "not affiliated",
    "not endorsed",
    "unofficial",
]
NEGATION_PREFIXES = ["不", "未", "无", "非", "not ", "no "]


def _is_negated(text: str, pos: int) -> bool:
    """判定 pos 处的匹配是否处于否定语境（前 8 字符内出现否定词）。"""
    window = text[max(0, pos - 8):pos].lower()
    return any(prefix.lower() in window for prefix in NEGATION_PREFIXES)


def _readme_text() -> str:
    assert README.exists(), (
        f"运行说明缺失：{README} 不存在（REQ-16 要求安装/启动/模型配置与禁用/"
        "支持期间/维护/免责说明）"
    )
    return README.read_text(encoding="utf-8")


def test_readme_install_run_model_toggle() -> None:
    """README 覆盖安装、启动（单 worker 回环）、模型配置与禁用、支持期间、免责。"""
    text = _readme_text()

    # —— 安装（§8：venv＋版本检查＋pip install -e）——
    assert "venv" in text, "README 必须含虚拟环境创建说明（§8）"
    assert "pip install -e" in text, "README 必须含可编辑安装说明（§8）"

    # —— 启动（§8：回环＋单 worker＋无代理头）——
    assert "uvicorn app.main:app" in text, "README 必须含启动命令（§8）"
    assert "--workers 1" in text, "启动命令必须强制 --workers 1（§8/C5.1）"
    assert "--no-proxy-headers" in text, "启动命令必须 --no-proxy-headers（C5.1）"
    assert "--host 127.0.0.1" in text, "启动命令必须回环绑定（C5.1）"

    # —— 模型配置与禁用（C4.2：env 密钥；不配置即可纯本地）——
    assert "HKTAX_MODEL_API_KEY" in text, (
        "README 必须说明模型密钥 env HKTAX_MODEL_API_KEY（C4.2）"
    )
    assert ("禁用" in text) or ("disable" in text.lower()), (
        "README 必须说明模型可禁用／不配置时的本地计算可用性（REQ-16）"
    )

    # —— 支持期间（§1：2024/25–2026/27）——
    assert ("2024/25" in text) or ("2024_25" in text), (
        "README 必须列支持期间起点（REQ-16）"
    )
    assert ("2026/27" in text) or ("2026_27" in text), (
        "README 必须列支持期间终点（REQ-16）"
    )

    # —— 维护与免责 ——
    assert "维护" in text, "README 必须含维护说明（REQ-16）"
    assert ("免责" in text) or ("免責" in text) or ("disclaimer" in text.lower()), (
        "README 必须含免责声明（REQ-16）"
    )


def test_no_ird_endorsement() -> None:
    """全文不得出现未被否定的 IRD 认可/认证宣称；且必须存在非背书声明。"""
    text = _readme_text()

    for pattern in ENDORSEMENT_PATTERNS:
        for match in re.finditer(re.escape(pattern), text, flags=re.IGNORECASE):
            assert _is_negated(text, match.start()), (
                f"README 出现未否定的背书宣称 {pattern!r}"
                f"（上下文：{text[max(0, match.start() - 20):match.end() + 20]!r}）"
                "——REQ-16 禁止宣称 IRD 认证"
            )

    assert any(marker in text for marker in NON_ENDORSEMENT_MARKERS), (
        "README 必须含明确的非官方/不背书声明（REQ-16）"
    )
