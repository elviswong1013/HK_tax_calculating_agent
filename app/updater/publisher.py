"""发布与回滚（SDD §5 发布/回滚行；Annex C C5.6/C5.8/C8.4；REQ-18）。

REQ: 18（§9：发布＝单事务原子指针切换＋audit；全验证自动发布（无点击）；
回滚仅审计化指针、不法律时间旅行——不得把已失效旧规则宣称为现行有效，
受影响期间拒算并说明）。
规格锚点:
  - SDD §5：发布＝单事务内（插入 bundle＋publication＋指针切换），无新旧参数
    混用；回滚仅审计化指针回退到此前已发布版本，写 audit_log。
  - Annex C C5.6：check/publish/rollback 三者互斥；单事务原子。
  - Annex C C5.8：audit_log 四类事件（rule/check/publish/rollback）。
  - Annex C C8.4：空报告/未通过门禁 → 不得发布。
  - 005 §15.4/§15.5：publisher 按 purpose/实际 diff 重算 required，强制
    candidate/manifest/evidence/proof/case receipt/static/isolation 绑定；
    five-tax fixed profile 与 rollback 目标 profile 准入。
期望值来源: 结构性契约（原子性/审计/拒绝语义），无金额期望。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.core.errors import AppError
from app.core.hashing import bundle_content_hash, canonical_json_bytes

_PERIOD_RE = re.compile(r"(?<!\d)(\d{4}_\d{2})(?!\d)")


def _canonical_sha(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _subcase_expected_payload(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise ValueError("expected_subcases 必须为列表")
    payload: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("expected_subcases 元素必须为映射")
        subcase_id = entry.get("subcase_id")
        input_data = entry.get("input")
        expected_data = entry.get("expected")
        if not isinstance(subcase_id, str) or not subcase_id:
            raise ValueError("expected_subcases.subcase_id 缺失")
        if not isinstance(input_data, Mapping) or not isinstance(
            expected_data, Mapping
        ):
            raise ValueError("expected_subcases input/expected 必须为映射")
        payload.append(
            {
                "subcase_id": subcase_id,
                "input": copy.deepcopy(dict(input_data)),
                "expected": copy.deepcopy(dict(expected_data)),
            }
        )
    return payload


def _subcase_actual_payload(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise ValueError("actual_subcases 必须为列表")
    payload: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("actual_subcases 元素必须为映射")
        subcase_id = entry.get("subcase_id")
        input_data = entry.get("input")
        expected_data = entry.get("expected")
        actual_data = entry.get("actual")
        receipt = entry.get("receipt")
        passed = entry.get("passed")
        if not isinstance(subcase_id, str) or not subcase_id:
            raise ValueError("actual_subcases.subcase_id 缺失")
        if not isinstance(input_data, Mapping) or not isinstance(
            expected_data, Mapping
        ):
            raise ValueError("actual_subcases input/expected 必须为映射")
        if not isinstance(actual_data, Mapping) or not isinstance(receipt, Mapping):
            raise ValueError("actual_subcases actual/receipt 必须为映射")
        if not isinstance(passed, bool):
            raise ValueError("actual_subcases.passed 必须为布尔值")
        payload.append(
            {
                "subcase_id": subcase_id,
                "input": copy.deepcopy(dict(input_data)),
                "expected": copy.deepcopy(dict(expected_data)),
                "actual": copy.deepcopy(dict(actual_data)),
                "passed": passed,
                "receipt": copy.deepcopy(dict(receipt)),
            }
        )
    return payload


def _subcase_proof_fields(
    expected_payload: Sequence[Mapping[str, Any]],
    actual_payload: Sequence[Mapping[str, Any]],
    subcase_ids: Sequence[str],
) -> dict[str, Any]:
    expected_sha = _canonical_sha([dict(entry) for entry in expected_payload])
    actual_sha = _canonical_sha([dict(entry) for entry in actual_payload])
    ids = [str(value) for value in subcase_ids]
    combined_sha = _canonical_sha(
        {
            "expected_subcases_sha256": expected_sha,
            "actual_subcases_sha256": actual_sha,
            "subcase_ids": ids,
        }
    )
    return {
        "expected_subcases": [dict(entry) for entry in expected_payload],
        "actual_subcases": [dict(entry) for entry in actual_payload],
        "expected_subcases_sha256": expected_sha,
        "actual_subcases_sha256": actual_sha,
        "subcase_results_sha256": combined_sha,
    }


def _validate_subcase_proof(
    *,
    row: Mapping[str, Any],
    case_id: str,
    subcase_ids: Sequence[str],
) -> None:
    """B5 row 子 case 证明：payload/digest/ID/内部一致性（不重跑税引擎）。"""
    fields = (
        "expected_subcases",
        "actual_subcases",
        "expected_subcases_sha256",
        "actual_subcases_sha256",
        "subcase_results_sha256",
    )
    for field in fields:
        if field not in row:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} subcase proof missing {field}",
            )
    try:
        expected_payload = _subcase_expected_payload(row.get("expected_subcases"))
        actual_payload = _subcase_actual_payload(row.get("actual_subcases"))
    except (TypeError, ValueError) as exc:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            f"case {case_id!r} subcase proof shape invalid: {exc}",
        ) from exc
    plan_ids = [str(value) for value in subcase_ids]
    if [entry["subcase_id"] for entry in expected_payload] != plan_ids:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            f"case {case_id!r} expected subcase ids do not match production plan",
        )
    if [entry["subcase_id"] for entry in actual_payload] != plan_ids:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            f"case {case_id!r} actual subcase ids do not match production plan",
        )
    if len(expected_payload) != len(actual_payload):
        raise AppError(
            "E_UPDATE_QUARANTINED",
            f"case {case_id!r} subcase payload lengths differ",
        )
    for expected_entry, actual_entry in zip(expected_payload, actual_payload):
        subcase_id = expected_entry["subcase_id"]
        if expected_entry["input"] != actual_entry["input"]:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} subcase {subcase_id!r} input binding mismatch",
            )
        # expected_subcases 是独立 harness 期望；actual_subcases 内的 expected
        # 是候选引擎本地静态期望（AVD proof=110 时二者合法不同）。publisher 只
        # 校验 canonical payload/digest/ID 与收据一致性，不重跑税引擎，也不把
        # 两个 expected 绑成同一值。
        receipt = actual_entry["receipt"]
        if receipt.get("subcase_id") != subcase_id:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} subcase {subcase_id!r} receipt identity mismatch",
            )
        if not isinstance(receipt.get("engine"), str) or not receipt["engine"]:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} subcase {subcase_id!r} receipt engine missing",
            )
        if receipt.get("input_sha256") != _canonical_sha(
            dict(actual_entry["input"])
        ):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} subcase {subcase_id!r} receipt input hash mismatch",
            )
        local_match = all(
            actual_entry["actual"].get(key) == value
            for key, value in actual_entry["expected"].items()
        )
        if actual_entry["passed"] != local_match:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} subcase {subcase_id!r} passed is inconsistent",
            )
    recomputed = _subcase_proof_fields(
        expected_payload, actual_payload, plan_ids
    )
    for key in (
        "expected_subcases_sha256",
        "actual_subcases_sha256",
        "subcase_results_sha256",
    ):
        if row.get(key) != recomputed[key]:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} subcase {key} does not match bound payload",
            )


def _reference_code_sha256() -> str:
    """当前 reference.py 文件字节 SHA256（不用 canonical label）。"""
    from app.updater import reference as reference_module

    return "sha256:" + hashlib.sha256(
        Path(reference_module.__file__).read_bytes()
    ).hexdigest()


def _profile_rows(content: Mapping[str, Any]) -> list[dict[str, str]]:
    frozen = content.get("frozen_semantics")
    profiles = frozen.get("profiles") if isinstance(frozen, Mapping) else []
    rows: list[dict[str, str]] = []
    for profile in profiles or ():
        if not isinstance(profile, Mapping):
            raise AppError("E_UPDATE_QUARANTINED", "候选 frozen profile 形状非法")
        digest = profile.get("semantic_digest") or _canonical_sha(dict(profile))
        rows.append(
            {
                "profile_id": str(profile.get("profile_id")),
                "semantic_digest": str(digest),
            }
        )
    return rows


def _changed_data_paths(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> list[str]:
    changed: list[str] = []

    def walk(node_a: Any, node_b: Any, prefix: list[str]) -> None:
        if isinstance(node_a, dict) and isinstance(node_b, dict):
            for key in sorted(set(node_a) | set(node_b)):
                walk(node_a.get(key), node_b.get(key), prefix + [str(key)])
        elif isinstance(node_a, list) and isinstance(node_b, list):
            if len(node_a) != len(node_b):
                changed.append(".".join(prefix))
            else:
                for index, (item_a, item_b) in enumerate(zip(node_a, node_b)):
                    walk(item_a, item_b, prefix + [str(index)])
        elif node_a != node_b:
            changed.append(".".join(prefix))

    walk(baseline.get("data"), candidate.get("data"), [])
    return sorted(path for path in changed if path)


def _required_ids(report: Mapping[str, Any]) -> list[str]:
    binding = report.get("binding") if isinstance(report.get("binding"), Mapping) else {}
    cases = report.get("cases") if isinstance(report.get("cases"), Mapping) else {}
    binding_ids = [str(value) for value in (binding.get("required_case_ids") or [])]
    case_ids = [str(value) for value in (cases.get("required_case_ids") or [])]
    if binding_ids != case_ids:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            "门禁报告 required 集合在 binding/cases 间不一致（拒绝自减）",
        )
    return binding_ids


def _require_nonempty_str(value: Any, *, field: str, token: str) -> str:
    if not isinstance(value, str) or not value:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            f"{token} 证明缺失/非法（{field} 必须为非空字符串）",
        )
    return value


def _require_mapping(value: Any, *, token: str, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AppError(
            "E_UPDATE_QUARANTINED",
            f"{token} 证明缺失/非法（{field} 必须为映射）",
        )
    return value


def _validate_gate_environment(report: Mapping[str, Any]) -> None:
    gate = _require_mapping(
        report.get("gate_environment"), token="isolation", field="gate_environment"
    )
    for key in (
        "network_calls",
        "direct_http_attempts",
        "socket_attempts",
        "dns_attempts",
        "session_creations",
        "pointer_write_attempts",
        "outside_temp_db",
        "thread_starts",
        "subprocess_starts",
    ):
        value = gate.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value != 0:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"gate isolation evidence non-zero/missing: {key}={value!r}",
            )
    if gate.get("observers_restored") is not True:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            "gate isolation evidence: observers_restored must be true",
        )
    if gate.get("user_session") is not False:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            "gate isolation evidence: user_session must be false",
        )
    if gate.get("current_pointer_written") is not False:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            "gate isolation evidence: current_pointer_written must be false",
        )


def _validate_case_rows(
    *,
    report: Mapping[str, Any],
    required: Sequence[str],
    evidence_refs: Sequence[Mapping[str, Any]],
) -> None:
    from app.updater.reference import B5_CASES

    cases = _require_mapping(report.get("cases"), token="case", field="cases")
    rows = cases.get("rows")
    if not isinstance(rows, list) or not rows:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            "gate report missing candidate case receipts (cases.rows)",
        )
    if cases.get("per_stage_consistent") is not True:
        raise AppError("E_UPDATE_QUARANTINED", "candidate case stages inconsistent")
    evidence_ids = {
        str(fact.get("fact_id"))
        for fact in evidence_refs or ()
        if isinstance(fact, Mapping) and fact.get("fact_id")
    }
    by_case = {
        str(row.get("case_id")): row
        for row in rows
        if isinstance(row, Mapping) and row.get("case_id")
    }
    for case_id in required:
        row = by_case.get(str(case_id))
        if row is None:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"gate report missing case receipt for {case_id!r}",
            )
        if row.get("mode") != "candidate":
            raise AppError("E_UPDATE_QUARANTINED", f"case {case_id!r} mode must be candidate")
        if row.get("passed") is not True or row.get("stage_match") is not True:
            raise AppError("E_UPDATE_QUARANTINED", f"case {case_id!r} not passed/stage-matched")
        plan = B5_CASES.get(str(case_id)) or {}
        expected_subcases = list(plan.get("subcase_ids") or [])
        if list(row.get("subcase_ids") or []) != expected_subcases:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} subcase_ids do not match production plan",
            )
        if str(case_id) in B5_CASES:
            _validate_subcase_proof(
                row=row,
                case_id=str(case_id),
                subcase_ids=expected_subcases,
            )
        expected_input_proof = _canonical_sha(
            {"case_id": str(case_id), "subcase_ids": expected_subcases}
        )
        if row.get("input_proof_sha256") != expected_input_proof:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} input_proof_sha256 does not match production plan",
            )
        expected_worksheet = _canonical_sha(
            {"case_id": str(case_id), "path": plan.get("path")}
        )
        if row.get("worksheet_sha256") != expected_worksheet:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} worksheet_sha256 does not match production plan",
            )
        refs = row.get("parameter_evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} missing parameter_evidence_refs",
            )
        if not set(str(ref) for ref in refs).issubset(evidence_ids):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"case {case_id!r} parameter_evidence_refs not bound to independent facts",
            )


def _validate_regression(report: Mapping[str, Any]) -> None:
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT
    from app.updater.reference import REQUIRED_CASES

    regression = _require_mapping(
        report.get("regression"), token="static|regression", field="regression"
    )
    if regression.get("failed") not in (0,):
        raise AppError(
            "E_UPDATE_QUARANTINED",
            "static regression failed must be zero",
        )
    if list(regression.get("static_case_ids") or []) != list(REQUIRED_CASES):
        raise AppError(
            "E_UPDATE_QUARANTINED",
            "static regression case ids do not match release set",
        )
    expected_baseline = bundle_content_hash(INITIAL_BUNDLE_CONTENT)
    if regression.get("static_baseline_bundle_hash") != expected_baseline:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            "static regression baseline hash does not match frozen release baseline",
        )
    passed = regression.get("passed")
    if not isinstance(passed, int) or passed < len(REQUIRED_CASES):
        raise AppError(
            "E_UPDATE_QUARANTINED",
            "static regression passed count incomplete",
        )


def publish_update(
    store,
    *,
    job_id: str,
    content: Mapping[str, Any],
    evidence_refs: Sequence[Mapping[str, Any]],
    validation_report: Mapping[str, Any] | None,
    purpose: str,
    baseline: Mapping[str, Any] | None,
    manifests: Sequence[Mapping[str, Any]] = (),
    required_cases: Sequence[str] = (),
) -> dict[str, str]:
    """门禁报告门（C8.4）＋store 唯一锁/单事务发布＋publish 类 audit。

    可信输入（不接受缺失/自证推断）：purpose 必填；update 的 baseline
    id/hash/schema 必填且与报告 binding 一致；required 集合由服务端按
    purpose/实际 diff 重算（报告只能等值，不得自增/自减）；candidate/
    manifest/evidence/proof/case receipts/static/isolation 全部绑定；
    seed 必须匹配发行冻结候选。任何不符在事务内零切换/零部分写。
    """
    from app.config import ENGINE_VERSION
    from app.rules.admission import FIXED_PROFILE_ID_BY_TAX, verify_fixed_profile
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT
    from app.updater.reference import AFFECTED_CASES, REQUIRED_CASES

    # ACC-1：核验开始即冻结候选；后续所有核验与写入只使用该快照，调用方
    # 在此之后修改原 dict 不得影响发布内容/已核验 hash。
    try:
        frozen_content = json.loads(
            json.dumps(dict(content), ensure_ascii=False)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bundle content 缺少封闭字段（C2.8）：{exc}") from exc
    # R1：evidence 与候选同一时点冻结；后续 evidence 校验、binding digest
    # 与 store 写入只使用该深快照，调用方后续修改原列表/dict 不影响发布。
    try:
        frozen_evidence = copy.deepcopy(
            [dict(fact) for fact in evidence_refs or ()]
        )
    except (TypeError, ValueError) as exc:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            f"evidence refs 形状非法，无法冻结（evidence freeze）：{exc}",
        ) from exc
    verified_evidence_sha = _canonical_sha(frozen_evidence)
    if purpose not in {"seed", "update"}:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            f"发布 purpose 非法（须为 seed|update）：{purpose!r}",
        )
    report = dict(validation_report or {})
    if report.get("purpose") != purpose:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            (
                "门禁报告 purpose 缺失/不符（不得以旧报告充当发布证明）："
                f"report={report.get('purpose')!r}，request={purpose!r}"
            ),
        )
    binding = _require_mapping(
        report.get("binding"), token="binding", field="binding"
    )
    report_baseline_id = binding.get("baseline_bundle_id")
    report_baseline_hash = binding.get("baseline_bundle_hash")
    report_baseline_schema = binding.get("baseline_rules_schema_version")
    if purpose == "seed":
        if (
            baseline is not None
            or report_baseline_id is not None
            or report_baseline_hash is not None
            or report_baseline_schema is not None
        ):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "seed 发布 baseline 必须两项 null（禁止单 null/非空组合）",
            )
    else:
        if not isinstance(baseline, Mapping):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "update 发布必须提供当前 baseline（id/hash/schema）",
            )
        expected_baseline = (
            baseline.get("bundle_id"),
            baseline.get("bundle_hash"),
            baseline.get("rules_schema_version"),
        )
        actual_baseline = (
            report_baseline_id,
            report_baseline_hash,
            report_baseline_schema,
        )
        if not all(
            isinstance(value, str) and value for value in expected_baseline
        ):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "update baseline id/hash/schema 必须全部为非空字符串",
            )
        if tuple(actual_baseline) != tuple(expected_baseline):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "门禁报告 baseline 与当前实际 baseline 不符（拒绝复用旧报告）",
            )
    tests = report.get("tests")
    passed = tests.get("passed", 0) if isinstance(tests, Mapping) else 0
    failed = tests.get("failed", 0) if isinstance(tests, Mapping) else 0
    if (
        report.get("decision") != "publish"
        or not isinstance(passed, int)
        or passed <= 0
        or (isinstance(failed, int) and failed != 0)
    ):
        raise AppError(
            "E_UPDATE_QUARANTINED",
            (
                "发布被拒绝（C8.4 门禁报告门）："
                f"decision={report.get('decision')!r}，tests.passed={passed!r}，"
                f"tests.failed={failed!r}"
            ),
        )
    if str(frozen_content.get("rules_schema_version")) != "2.0.0":
        raise AppError(
            "E_BUNDLE_INCOMPATIBLE",
            (
                "发布候选 rules_schema_version 必须为 2.0.0："
                f"{frozen_content.get('rules_schema_version')!r}"
            ),
        )
    try:
        candidate_hash = bundle_content_hash(frozen_content)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bundle content 缺少封闭字段（C2.8）：{exc}") from exc
    if purpose == "update":
        reported_candidate_hash = _require_nonempty_str(
            binding.get("candidate_bundle_hash"),
            field="binding.candidate_bundle_hash",
            token="candidate",
        )
        if reported_candidate_hash != candidate_hash:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "候选内容 hash 与门禁报告绑定不符（拒绝发布被替换的候选）",
            )
        manifest_rows = [dict(manifest) for manifest in manifests]
        expected_manifests_sha = _canonical_sha(manifest_rows)
        reported_manifests_sha = _require_nonempty_str(
            binding.get("manifests_sha256"),
            field="binding.manifests_sha256",
            token="manifest",
        )
        if reported_manifests_sha != expected_manifests_sha:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "发布 manifests 与门禁报告绑定不符（拒绝替换 manifest）",
            )
        expected_evidence_sha = verified_evidence_sha
        reported_evidence_sha = _require_nonempty_str(
            binding.get("evidence_sha256"),
            field="binding.evidence_sha256",
            token="evidence|proof",
        )
        if reported_evidence_sha != expected_evidence_sha:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "independent evidence/proof summary does not match supplied facts",
            )
        reported_reference_code = _require_nonempty_str(
            binding.get("reference_code_sha256"),
            field="binding.reference_code_sha256",
            token="proof",
        )
        if reported_reference_code != _reference_code_sha256():
            raise AppError(
                "E_UPDATE_QUARANTINED",
                (
                    "reference code hash does not match current reference.py "
                    "file bytes"
                ),
            )
        if binding.get("engine_version") != ENGINE_VERSION:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "gate report engine_version does not match current engine",
            )
        if binding.get("rules_schema_version") != "2.0.0":
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "gate report rules_schema_version must be 2.0.0",
            )
        expected_profiles = _profile_rows(frozen_content)
        if list(binding.get("profiles") or []) != expected_profiles:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "gate report profile bindings do not match candidate frozen profiles",
            )

    if purpose == "seed":
        reported_candidate_hash = _require_nonempty_str(
            binding.get("candidate_bundle_hash"),
            field="binding.candidate_bundle_hash",
            token="candidate",
        )
        if reported_candidate_hash != candidate_hash:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "seed candidate hash does not match the supplied release content",
            )
        manifest_rows = [dict(manifest) for manifest in manifests]
        reported_manifests_sha = _require_nonempty_str(
            binding.get("manifests_sha256"),
            field="binding.manifests_sha256",
            token="manifest",
        )
        if reported_manifests_sha != _canonical_sha(manifest_rows):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "seed manifests do not match the gate report binding",
            )
        reported_evidence_sha = _require_nonempty_str(
            binding.get("evidence_sha256"),
            field="binding.evidence_sha256",
            token="evidence|proof",
        )
        if reported_evidence_sha != verified_evidence_sha:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "seed independent evidence/proof summary does not match supplied facts",
            )
        reported_reference_code = _require_nonempty_str(
            binding.get("reference_code_sha256"),
            field="binding.reference_code_sha256",
            token="proof",
        )
        if reported_reference_code != _reference_code_sha256():
            raise AppError(
                "E_UPDATE_QUARANTINED",
                (
                    "seed reference code hash does not match current "
                    "reference.py file bytes"
                ),
            )
        if binding.get("engine_version") != ENGINE_VERSION:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "seed gate report engine_version does not match current engine",
            )
        if binding.get("rules_schema_version") != "2.0.0":
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "seed gate report rules_schema_version must be 2.0.0",
            )
        if list(binding.get("profiles") or []) != _profile_rows(frozen_content):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "seed gate report profile bindings do not match frozen release profiles",
            )
        if _required_ids(report) != list(REQUIRED_CASES):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "seed required case set does not match release REQUIRED_CASES",
            )
        release_hash = bundle_content_hash(INITIAL_BUNDLE_CONTENT)
        if candidate_hash != release_hash:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                (
                    "seed 候选不是冻结发行产物（release/frozen candidate hash "
                    "mismatch）"
                ),
            )
    else:
        assert isinstance(baseline, Mapping)
        baseline_id = str(baseline.get("bundle_id") or "")
        baseline_content = store.get_bundle(baseline_id)
        changed_paths = _changed_data_paths(baseline_content, frozen_content)
        if not changed_paths:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "update 发布无可识别数据 diff（required 集合无法重算）",
            )
        unknown = [path for path in changed_paths if path not in AFFECTED_CASES]
        if unknown:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"changed paths without affected case coverage: {unknown!r}",
            )
        expected_required = sorted(
            {AFFECTED_CASES[path] for path in changed_paths}
        )
        report_required = _required_ids(report)
        configured_required = sorted(str(case_id) for case_id in required_cases)
        if (
            sorted(report_required) != expected_required
            or configured_required != expected_required
        ):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                (
                    "required set not recomputed from purpose/diff: "
                    f"expected={expected_required!r} report={report_required!r} "
                    f"server={configured_required!r}"
                ),
            )
        coverage = _require_mapping(
            report.get("coverage"), token="coverage", field="coverage"
        )
        if sorted(str(path) for path in (coverage.get("changed_paths") or [])) != sorted(
            changed_paths
        ):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "coverage.changed_paths does not match actual candidate diff",
            )
        if sorted(str(case_id) for case_id in (coverage.get("affected_case_ids") or [])) != expected_required:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "coverage.affected_case_ids does not match recomputed required set",
            )
        if list(coverage.get("static_case_ids") or []) != list(REQUIRED_CASES):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "coverage.static_case_ids does not match release static set",
            )
        _validate_case_rows(
            report=report,
            required=expected_required,
            evidence_refs=frozen_evidence,
        )
        _validate_regression(report)
        _validate_gate_environment(report)
    for tax_type in FIXED_PROFILE_ID_BY_TAX:
        # 五税固定 profile 全部核验（shared P 在每次调用内亦核验）。
        verify_fixed_profile(frozen_content, tax_type=tax_type)
    return store.publish_update(
        purpose=purpose,
        baseline=baseline,
        content=frozen_content,
        evidence_refs=frozen_evidence,
        required_cases=tuple(required_cases),
        publish_audit={"action": "update_publish", "job_id": job_id},
        verified_candidate_hash=candidate_hash,
        verified_evidence_sha256=verified_evidence_sha,
    )


def _affected_periods(
    content_a: Mapping[str, Any], content_b: Mapping[str, Any]
) -> list[str]:
    """两版本 data 差所涉课税年度键（路径中 YYYY_NN 形态，如 2025_26）。"""
    differing: list[list[str]] = []

    def walk(node_a: Any, node_b: Any, prefix: list[str]) -> None:
        if isinstance(node_a, dict) and isinstance(node_b, dict):
            for key in sorted(set(node_a) | set(node_b)):
                walk(node_a.get(key), node_b.get(key), prefix + [str(key)])
        elif isinstance(node_a, list) and isinstance(node_b, list):
            if len(node_a) != len(node_b):
                differing.append(prefix)
            else:
                for index, (item_a, item_b) in enumerate(zip(node_a, node_b)):
                    walk(item_a, item_b, prefix + [str(index)])
        elif node_a != node_b:
            differing.append(prefix)

    walk(content_a.get("data"), content_b.get("data"), [])
    periods: set[str] = set()
    for path in differing:
        periods.update(_PERIOD_RE.findall(".".join(path)))
    return sorted(periods)


def rollback_to(store, *, target_bundle_id: str, reason: str) -> dict[str, Any]:
    """回滚＝审计化指针回退到此前已发布版本（store 单事务）；不法律时间旅行。

    未知/未发布目标 → AppError(code="E_UPDATE_ROLLBACK_INVALID")。
    返回 {"rolled_back_to", "from_bundle_id", "affected_periods", "notice"}；
    affected_periods 随 rollback 类 audit 持久（availability 据此阻断受影响
    期间；重启/回滚不清，Annex C C5.6）。目标 bundle 内容不被改写；store
    在同一事务内执行目标固定 profile 准入。
    """
    result = store.rollback_atomic(
        target_bundle_id=target_bundle_id,
        reason=reason,
        affected_periods_fn=_affected_periods,
    )
    affected = list(result["affected_periods"])
    return {
        "rolled_back_to": result["rolled_back_to"],
        "from_bundle_id": result["from_bundle_id"],
        "affected_periods": affected,
        "notice": (
            f"已回滚至此前已发布版本 {target_bundle_id}；"
            f"受影响期间（{'、'.join(affected) if affected else '无'}）的现行计算"
            "已拒绝，不把旧规则宣称为现行有效；待重新核验并发布后恢复。"
        ),
    }
