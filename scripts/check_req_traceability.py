#!/usr/bin/env python3
"""REQ 追溯脚本：编号需求（REQ-1..REQ-18）→ 测试覆盖映射与门禁（REQ-15；SDD §8）。

用法：
    python scripts/check_req_traceability.py                  # 人读报告（退出码 0）
    python scripts/check_req_traceability.py --json           # JSON 报告（恒退出码 0）
    python scripts/check_req_traceability.py --require REQ-1,REQ-9
        # 门禁模式：列表内任一需求未覆盖 → 退出码非 0

扫描规则：tests/ 下文件名以 test_ 开头的 .py；文件级 REQ 标记归入该文件全部
测试函数（def test_*）；conftest.py 与非 test_ 前缀文件忽略。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
TOTAL_REQS = 18  # SDD §9：编号需求总数（AC-1..AC-18 一一对应）

_REQ_MARKER = re.compile(r"(?<![A-Za-z])REQ\s*[-:：]?\s*([0-9]{1,2})")
_TEST_DEF = re.compile(r"^def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)


def collect_report() -> dict[str, dict[str, object]]:
    report: dict[str, dict[str, object]] = {
        f"REQ-{number}": {"covered": False, "tests": []}
        for number in range(1, TOTAL_REQS + 1)
    }
    if not TESTS_DIR.is_dir():
        return report
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if not path.name.startswith("test_"):
            continue  # conftest.py 与非 test_ 前缀文件忽略
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="编号需求 → 测试覆盖追溯检查（REQ-15）"
    )
    parser.add_argument(
        "--json", action="store_true", help="输出 JSON 映射（恒退出码 0）"
    )
    parser.add_argument(
        "--require",
        default=None,
        help="逗号分隔的编号需求清单；任一未覆盖 → 退出码 1",
    )
    args = parser.parse_args(argv)
    report = collect_report()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.require:
        requested = [item.strip() for item in args.require.split(",") if item.strip()]
        missing: list[str] = []
        for req_id in requested:
            entry = report.get(req_id)
            covered = bool(entry and entry["covered"])
            print(f"{req_id}: {'covered' if covered else 'NOT COVERED'}")
            if not covered:
                missing.append(req_id)
        if missing:
            print(
                "FAIL: 未覆盖需求 " + ", ".join(missing),
                file=sys.stderr,
            )
            return 1
        return 0

    for number in range(1, TOTAL_REQS + 1):
        req_id = f"REQ-{number}"
        entry = report[req_id]
        tests = entry["tests"]
        assert isinstance(tests, list)
        state = "covered" if entry["covered"] else "NOT COVERED"
        print(f"{req_id}: {state} ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
