"""002 W2 script adaptation guards (traceability / report / offline compare).

REQ: REQ-13（追溯命名空间与证据）、REQ-12（固定束/版本/provenance）。
规格锚点：002 §7（命令）、006 §4.5（报告与版本适配）、006 §2（追溯新 CLI）。
本文件只验证脚本接口/防护/离线语义；不触网、不启动 live 驱动。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TRACE_SCRIPT = ROOT / "scripts" / "check_req_traceability.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run_trace(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TRACE_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
    )


def test_002_traceability_namespace_json_and_gate() -> None:
    """002 命名空间 JSON 为 13 键；--require-002 全绿/缺号失败；001 接口保留。"""
    result = _run_trace("--spec", "002", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["spec"] == "002"
    assert payload["namespace"] == "002-tax-correctness-remediation"
    requirements = payload["requirements"]
    assert set(requirements) == {f"002:REQ-{n}" for n in range(1, 14)}
    assert all(entry["covered"] for entry in requirements.values())
    for entry in requirements.values():
        assert entry["tests"], entry
        assert "node" in entry["evidence_sources"]

    gate = _run_trace("--require-002", ",".join(str(n) for n in range(1, 14)))
    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert "002:REQ-13: covered" in gate.stdout

    missing = _run_trace("--require-002", "1,14")
    assert missing.returncode != 0

    legacy = _run_trace("--json")
    assert legacy.returncode == 0, legacy.stderr
    legacy_payload = json.loads(legacy.stdout)
    assert set(legacy_payload) == {f"REQ-{n}" for n in range(1, 19)}
    assert all(entry["covered"] for entry in legacy_payload.values())
    assert not any(key.startswith("002:") for key in legacy_payload)

    legacy_gate = _run_trace("--require", "REQ-1,REQ-9,REQ-15")
    assert legacy_gate.returncode == 0, legacy_gate.stdout + legacy_gate.stderr


def test_002_traceability_node_level_evidence(tmp_path: Path) -> None:
    """002 只认显式 REQ 声明并逐节点列出；旧 001 文件不混入 002。"""
    trace = _load_module("check_req_traceability_w2", TRACE_SCRIPT)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_002_demo.py").write_text(
        '"""REQ: REQ-1 模块级声明。"""\n\n'
        "def test_002_demo_one() -> None:\n"
        '    """REQ: REQ-2 节点级声明。"""\n'
        "    assert True\n\n"
        "def test_002_demo_two() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )
    (tests_dir / "test_old.py").write_text(
        '"""REQ: REQ-9 旧 001 命名空间。"""\n\n'
        "def test_old_case() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )

    report = trace.collect_report_002(tests_dir)
    req1 = report["002:REQ-1"]
    assert req1["covered"] is True
    assert {node.split("::")[1] for node in req1["tests"]} == {
        "test_002_demo_one",
        "test_002_demo_two",
    }
    req2 = report["002:REQ-2"]
    assert req2["covered"] is True
    assert [node.split("::")[1] for node in req2["tests"]] == ["test_002_demo_one"]
    assert req2["evidence_sources"]["node"] == 1
    assert report["002:REQ-9"]["covered"] is False


def test_002_generate_report_owner_guard(tmp_path: Path) -> None:
    """owner=002 必须写 reports/ 且不得覆盖 001；owner=001 默认保持历史路径。"""
    generate = _load_module(
        "generate_validation_report_w2",
        ROOT / "scripts" / "generate_validation_report.py",
    )
    assert generate.resolve_output_path(None, "001") == generate.OUTPUT_PATH
    allowed = tmp_path / "reports" / "002-engine-io.md"
    assert generate.resolve_output_path(str(allowed), "002") == allowed.resolve()
    with pytest.raises(ValueError):
        generate.resolve_output_path(None, "002")
    with pytest.raises(ValueError):
        generate.resolve_output_path(str(tmp_path / "flat" / "002.md"), "002")
    with pytest.raises(ValueError):
        generate.resolve_output_path(str(generate.OUTPUT_PATH), "002")


def test_002_generate_report_v2_outputs_to_ev_reports(tmp_path: Path) -> None:
    """v2 facts 可实跑；owner=002 报告写 EV/reports 且 001 字节不变。"""
    generate = _load_module("generate_validation_report_w2_run", ROOT / "scripts" / "generate_validation_report.py")
    legacy = generate.OUTPUT_PATH
    before = hashlib.sha256(legacy.read_bytes()).hexdigest()
    output = tmp_path / "reports" / "002-engine-io.md"
    assert generate.main(["--owner", "002", "--output", str(output)]) == 0
    text = output.read_text(encoding="utf-8")
    assert "Owner：`002`" in text
    assert "固定束核验/模拟，不代表实时规则可用性" in text
    assert "PAM39 Q2 PST（2026/27）" in text
    assert "PA Q32（2025/26）" in text
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before


def test_002_run_all_offline_default_and_assertions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认 offline：不调用 live；逐 case 断言；exit 0 有明确模拟标注。"""
    sys.path.insert(0, str(ROOT))
    run_all = importlib.import_module("scripts.official_calc_compare.run_all")

    def _forbidden_live(**_kwargs):
        raise AssertionError("offline 默认路径不得调用 live 驱动")

    monkeypatch.setattr(run_all, "_run_live", _forbidden_live)
    report = tmp_path / "reports" / "offline.md"
    assert run_all.main(["--report", str(report), "--owner", "test"]) == 0
    text = report.read_text(encoding="utf-8")
    assert "固定束核验/模拟，不代表实时规则可用性" in text
    assert "exit 0" in text and "不等于" in text
    assert "9 cases" in text
    assert not (tmp_path / "screenshots").exists()

    outcomes = run_all.run_all_cases()
    assert run_all.assert_fixed_bundle(outcomes) == []
    outcomes["salaries_2024_25"].value = "0"
    failures = run_all.assert_fixed_bundle(outcomes)
    assert any("salaries_2024_25.value" in failure for failure in failures)

    live_report = run_all.LIVE_REPORT_PATH
    before = hashlib.sha256(live_report.read_bytes()).hexdigest()
    assert run_all.main(["--report", str(live_report)]) == 2
    assert hashlib.sha256(live_report.read_bytes()).hexdigest() == before


def test_002_local_engine_context_provenance_and_values() -> None:
    """local_engine 逐 case 显式 context/provenance；2026/27 next 不回退。"""
    local_engine = _load_module(
        "official_calc_compare_local_engine_w2",
        ROOT / "scripts" / "official_calc_compare" / "local_engine.py",
    )
    outcomes = local_engine.run_all_cases()
    expected = {
        "salaries_2024_25": ("2440", "3940"),
        "salaries_2025_26": ("940", "3160"),
        "salaries_2026_27": ("3160", None),
        "avd_case1_20260601": ("4250000", None),
        "avd_case2_20250601": ("64856", None),
        "lease_6months": ("150", None),
        "stock_sale_notes_1000_01": ("4", None),
        "stock_other_transfer_doc": ("5", None),
        "stock_voluntary_1000_01": ("8", None),
    }
    for case, (value, next_provisional) in expected.items():
        outcome = outcomes[case]
        assert outcome.value == value
        assert outcome.next_provisional == next_provisional
        assert outcome.provenance["simulation"] is True
        assert outcome.provenance["bundle_hash"].startswith("sha256:")
    for case in ("salaries_2024_25", "salaries_2025_26", "salaries_2026_27"):
        availability = outcomes[case].context["availability"]
        assert availability["current"]["year"] == case.removeprefix("salaries_")
        assert availability["binding"]["bundle_hash"] == outcomes[case].provenance["bundle_hash"]
