#!/usr/bin/env python3
"""REQ 追溯脚本：编号需求（REQ-1..REQ-18）→ 测试覆盖映射与门禁（REQ-15；SDD §8）。

用法：
    python scripts/check_req_traceability.py                  # 001 报告（退出码 0）
    python scripts/check_req_traceability.py --json           # 001 JSON（恒退出码 0）
    python scripts/check_req_traceability.py --require REQ-1,REQ-9
        # 001 门禁模式：列表内任一需求未覆盖 → 退出码非 0
    python scripts/check_req_traceability.py --spec 002       # 002 报告
    python scripts/check_req_traceability.py --spec 002 --json
    python scripts/check_req_traceability.py --require-002 1,2,3
        # 002 门禁模式：列表内任一需求未覆盖 → 退出码非 0

命名空间：
    - 001（默认）：扫描 tests/ 下不以 test_002_ 开头的 test_*.py；文件级 REQ
      标记归入该文件全部测试函数（def test_*）；conftest.py 与非 test_ 前缀
      文件忽略。`--json` 恒为 18 键映射（REQ-1..REQ-18），保持历史接口。
    - 002：只扫描 tests/ 下 test_002_*.py；逐节点给出证据来源。002 的 REQ
      声明取模块/节点内的显式 `REQ:` 标记（如 `REQ: REQ-3`），不把旧 001
      文件名或注释中的 REQ-n 当行为覆盖。`--require-002` 接受 1..13。

本脚本只读 tests/ 与 specs/，不运行测试、不访问网络。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
TOTAL_REQS = 18  # 001 SDD §9：编号需求总数（AC-1..AC-18 一一对应）
TOTAL_REQS_002 = 13  # 002 SDD §8：REQ-1..REQ-13（AC-1..AC-13）
SPEC_002 = "002-tax-correctness-remediation"
SPEC_002_FILE_PREFIX = "test_002_"

_REQ_MARKER = re.compile(r"(?<![A-Za-z])REQ\s*[-:：]?\s*([0-9]{1,2})")
# 002 只接受显式声明（REQ: / REQ-1 / REQ-1:），不把随意的 REQ 字样当证据。
_REQ_DECLARATION_002 = re.compile(r"(?<![A-Za-z])REQ\s*[-:：]\s*([0-9]{1,2})")
_TEST_DEF = re.compile(r"^def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)
_TEST_DEF_ANY = re.compile(
    r"^(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE
)


def _is_002_test_file(path: Path) -> bool:
    return path.name.startswith(SPEC_002_FILE_PREFIX)


def _iter_001_test_files() -> Iterable[Path]:
    if not TESTS_DIR.is_dir():
        return
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if not path.name.startswith("test_"):
            continue  # conftest.py 与非 test_ 前缀文件忽略
        if _is_002_test_file(path):
            continue  # 002 命名空间不混入 001 追溯
        yield path


def collect_report() -> dict[str, dict[str, object]]:
    """001 命名空间：保持历史 18 键形状与文件级标记语义。"""
    report: dict[str, dict[str, object]] = {
        f"REQ-{number}": {"covered": False, "tests": []}
        for number in range(1, TOTAL_REQS + 1)
    }
    for path in _iter_001_test_files():
        text = path.read_text(encoding="utf-8")
        req_ids = {
            f"REQ-{int(match.group(1))}"
            for match in _REQ_MARKER.finditer(text)
            if 1 <= int(match.group(1)) <= TOTAL_REQS
        }
        if not req_ids:
            continue
        test_names = _TEST_DEF.findall(text)
        for req_id in req_ids:
            entry = report[req_id]
            entry["covered"] = True
            tests = entry["tests"]
            assert isinstance(tests, list)
            for name in test_names:
                if name not in tests:
                    tests.append(name)
    return report


def _module_and_node_reqs_002(text: str) -> tuple[set[int], dict[str, set[int]]]:
    """Return (module-level REQs, node -> node-level REQs) for one 002 file."""
    definitions = list(_TEST_DEF_ANY.finditer(text))
    module_end = definitions[0].start() if definitions else len(text)
    module_reqs = {
        int(match.group(1))
        for match in _REQ_DECLARATION_002.finditer(text[:module_end])
        if 1 <= int(match.group(1)) <= TOTAL_REQS_002
    }
    node_reqs: dict[str, set[int]] = {}
    for index, match in enumerate(definitions):
        end = definitions[index + 1].start() if index + 1 < len(definitions) else len(text)
        body = text[match.start():end]
        reqs = {
            int(item.group(1))
            for item in _REQ_DECLARATION_002.finditer(body)
            if 1 <= int(item.group(1)) <= TOTAL_REQS_002
        }
        node_reqs[match.group(1)] = reqs
    return module_reqs, node_reqs


def collect_report_002(tests_dir: Path | None = None) -> dict[str, dict[str, object]]:
    """002 命名空间：逐节点映射，REQ 证据只取显式声明。

    覆盖判定以节点为单位：任一 002 测试节点声明的 REQ（节点级优先；模块
    级 `REQ:` 声明作为该文件全部节点的显式命名空间证据）即计该 REQ 覆盖。
    节点级与模块级分开记录，便于审计区分强度。
    """
    root = tests_dir if tests_dir is not None else TESTS_DIR
    report: dict[str, dict[str, object]] = {
        f"002:REQ-{number}": {
            "covered": False,
            "tests": [],
            "evidence_sources": {"node": 0, "module": 0},
        }
        for number in range(1, TOTAL_REQS_002 + 1)
    }
    if not root.is_dir():
        return report
    for path in sorted(root.rglob("*.py")):
        if not _is_002_test_file(path):
            continue
        text = path.read_text(encoding="utf-8")
        module_reqs, node_reqs = _module_and_node_reqs_002(text)
        if not module_reqs and not any(node_reqs.values()):
            continue
        rel = path.relative_to(root.parent if root.name == "tests" else root).as_posix()
        for node, declared in node_reqs.items():
            reqs = set(declared) | set(module_reqs)
            if not reqs:
                continue
            for number in reqs:
                entry = report[f"002:REQ-{number}"]
                entry["covered"] = True
                tests = entry["tests"]
                assert isinstance(tests, list)
                node_id = f"{rel}::{node}"
                if node_id not in tests:
                    tests.append(node_id)
                sources = entry["evidence_sources"]
                assert isinstance(sources, dict)
                if number in declared:
                    sources["node"] = int(sources["node"]) + 1
                if number in module_reqs:
                    sources["module"] = int(sources["module"]) + 1
    return report


def _print_001_report(report: Mapping[str, Mapping[str, object]]) -> None:
    for number in range(1, TOTAL_REQS + 1):
        req_id = f"REQ-{number}"
        entry = report[req_id]
        tests = entry["tests"]
        assert isinstance(tests, list)
        state = "covered" if entry["covered"] else "NOT COVERED"
        print(f"{req_id}: {state} ({len(tests)} tests)")


def _print_002_report(report: Mapping[str, Mapping[str, object]]) -> None:
    for number in range(1, TOTAL_REQS_002 + 1):
        req_id = f"002:REQ-{number}"
        entry = report[req_id]
        tests = entry["tests"]
        sources = entry["evidence_sources"]
        assert isinstance(tests, list) and isinstance(sources, dict)
        state = "covered" if entry["covered"] else "NOT COVERED"
        print(
            f"{req_id}: {state} ({len(tests)} nodes; "
            f"node={sources['node']} module={sources['module']})"
        )


def _parse_require_002(value: str) -> list[str]:
    requested: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if item.startswith("REQ-"):
            item = item[4:]
        if not item.isdigit():
            raise ValueError(f"非法 002 需求编号：{item!r}")
        requested.append(f"002:REQ-{int(item)}")
    return requested


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="编号需求 → 测试覆盖追溯检查（001 默认；002 显式命名空间）"
    )
    parser.add_argument(
        "--spec",
        choices=("001", "002"),
        default="001",
        help="命名空间（默认 001；002 仅扫描 test_002_*.py）",
    )
    parser.add_argument(
        "--json", action="store_true", help="输出 JSON 映射（恒退出码 0）"
    )
    parser.add_argument(
        "--require",
        default=None,
        help="001：逗号分隔的编号需求清单；任一未覆盖 → 退出码 1",
    )
    parser.add_argument(
        "--require-002",
        default=None,
        help="002：逗号分隔的 1..13 清单；任一未覆盖 → 退出码 1",
    )
    args = parser.parse_args(argv)
    spec = "002" if args.require_002 is not None else args.spec

    if spec == "002":
        report = collect_report_002()
        namespace = SPEC_002
        total = TOTAL_REQS_002
    else:
        report = collect_report()
        namespace = "001-hk-tax-agent"
        total = TOTAL_REQS

    if args.json:
        payload = (
            {
                "spec": "002",
                "namespace": namespace,
                "requirements": report,
            }
            if spec == "002"
            else report
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if spec == "002":
        if args.require_002 is not None:
            try:
                requested = _parse_require_002(args.require_002)
            except ValueError as exc:
                print(f"FAIL: {exc}", file=sys.stderr)
                return 2
            missing: list[str] = []
            for req_id in requested:
                entry = report.get(req_id)
                covered = bool(entry and entry["covered"])
                print(f"{req_id}: {'covered' if covered else 'NOT COVERED'}")
                if not covered:
                    missing.append(req_id)
            if missing:
                print("FAIL: 未覆盖需求 " + ", ".join(missing), file=sys.stderr)
                return 1
            return 0
        _print_002_report(report)
        return 0

    if args.require:
        requested = [item.strip() for item in args.require.split(",") if item.strip()]
        missing = []
        for req_id in requested:
            entry = report.get(req_id)
            covered = bool(entry and entry["covered"])
            print(f"{req_id}: {'covered' if covered else 'NOT COVERED'}")
            if not covered:
                missing.append(req_id)
        if missing:
            print("FAIL: 未覆盖需求 " + ", ".join(missing), file=sys.stderr)
            return 1
        return 0

    _print_001_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
