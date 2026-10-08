"""REQ-15（本文档所属需求）追溯脚本行为：编号需求 → 测试覆盖映射与门禁。

REQ: 15（SDD §9：追溯脚本＋@acceptor；SDD §8 scripts/check_req_traceability.py）。
规格锚点:
  - SDD §8 文件布局与未来命令：`python scripts/check_req_traceability.py`。
  - SDD §9 编号需求共 18 项（AC-1..AC-18 一一对应）。
  - SDD §11 M1 里程碑：编号 1/2/9/10/14/16 基础面（＋本项）先行 Red。
期望值来源: 结构/行为断言（脚本输出与退出码语义）；无金额数值期望。

【拟名】被测契约（scripts/check_req_traceability.py CLI）:
  - `--json`：报告模式（恒退出码 0），stdout 输出 JSON 映射
    {"<需求编号>": {"covered": bool, "tests": [测试名…]}, …}，键集合恰为
    全部 18 个编号需求；扫描 tests/ 下 test_*.py 中的需求标记（文件级 REQ
    标记归入该文件全部测试函数；conftest 与非 test_ 前缀文件忽略）。
  - `--require <编号列表>`：门禁模式——列表内任一需求未覆盖 → 退出码非 0。
注: 本文件以 _req(n) 构造编号字面量，避免脚本把本文误计为其他需求的覆盖。

Red 说明: scripts/check_req_traceability.py 尚不存在 —— Red 失败原因为
「追溯脚本缺失」（文件不存在断言）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_req_traceability.py"
TOTAL_REQS = 18  # SDD §9：编号需求总数（AC 一一对应）


def _req(n: int) -> str:
    return f"REQ-{n}"


def _m1_set() -> list[str]:
    """M1 里程碑必须已覆盖的编号需求（SDD §11 M1 行＋本项）。"""
    return [_req(n) for n in (1, 2, 9, 10, 14, 15, 16)]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(SCRIPT.parents[1]),
    )


def test_traceability_all_reqs_covered() -> None:
    """报告覆盖全部 18 项编号需求；M1 集已覆盖；--require 门禁语义正确。"""
    assert SCRIPT.exists(), (
        f"追溯脚本缺失：{SCRIPT}（SDD §8 布局＋需求行要求追溯脚本）"
    )

    # —— 报告模式：键集合恰为全部 18 项 ——
    report = _run("--json")
    assert report.returncode == 0, report.stderr
    data = json.loads(report.stdout)
    assert set(data.keys()) == {_req(n) for n in range(1, TOTAL_REQS + 1)}, (
        f"追溯报告必须覆盖全部 {TOTAL_REQS} 项编号需求，实际键：{sorted(data)[:5]}…"
    )
    for req_id, entry in data.items():
        assert isinstance(entry, dict) and "covered" in entry and "tests" in entry, (
            f"{req_id} 条目必须含 covered/tests：{entry!r}"
        )

    # —— M1 覆盖集：已覆盖且有具名测试 ——
    for req_id in _m1_set():
        assert data[req_id]["covered"] is True, f"{req_id} 在 M1 必须已有测试覆盖"
        assert data[req_id]["tests"], f"{req_id} 的 tests 列表不得为空"

    # 具名测试锚点（SDD §10 计划名落位）
    req9 = _req(9)
    assert any(
        "test_decimal_canonical_string_only" in t for t in data[req9]["tests"]
    ), f"金额语法命名测试必须落在 {req9} 名下"
    req16 = _req(16)
    assert any(
        "test_readme_install_run_model_toggle" in t for t in data[req16]["tests"]
    ), f"README 命名测试必须落在 {req16} 名下"

    # —— 门禁模式：M1 集全过 → 0；未覆盖需求 → 非 0 ——
    gate_ok = _run("--require", ",".join(_m1_set()))
    assert gate_ok.returncode == 0, gate_ok.stdout + gate_ok.stderr

    not_yet = _req(3)  # M1 阶段尚无该需求的测试（M2 才引入）
    if not data[not_yet]["covered"]:
        gate_bad = _run("--require", not_yet)
        assert gate_bad.returncode != 0, (
            f"门禁模式：未覆盖需求 {not_yet} 必须使退出码非 0"
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
