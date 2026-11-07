"""tests/ai 共享假件与助手（M7 Red；Annex C C1 假件契约）。

本模块为测试基建：进程内假出站／假 HTTP 原语／wire 响应构造／AI 轮次与本地
计算助手，不新增第三方库；不在模块级导入 app.*（各测试/夹具内延迟导入
app.*，使 Red 阶段每个测试的失败原因＝「缺失的 app.* 模块或行为」，而非
收集期整体失败）。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 出站接缝 app.state.model_outbound（同 app.state.update_scheduler 模式）:
    对象持 send(upstream_endpoint: str, api_key: str, frozen: bytes) ->
    {"status_code": int, "body": bytes}；传输级失败以异常表达
    （httpx.TimeoutException＝超时；其余异常＝通信失败）；路由层在接缝
    存在时必须经它外发（生产默认＝真实 HTTPX content=frozen_bytes 出站，
    C4.1/C4.6 ⑦段）。
  - /api/v1/ai/chat 响应在模型返回 tool_calls 时必须执行 C3.6 工具层并回带
    tool_results：[{"id","name","outcome"∈{executed,rejected,ignored},
    "note","needs_confirmation","error","result"}]；纯文本轮必须标记
    unconfirmed=True（模型文本非权威，REQ-11/REQ-12）。
  - 纯文本模型文本永远不进入权威金额字段（amounts 六键仅来自工具结果）。
"""

from __future__ import annotations

import copy
import json

# —— 模型配置 env（C4.2：密钥仅服务端 env；endpoint/model 非秘密）——
MODEL_ENDPOINT_ENV = "HKTAX_MODEL_ENDPOINT"
MODEL_NAME_ENV = "HKTAX_MODEL_NAME"
MODEL_API_KEY_ENV = "HKTAX_MODEL_API_KEY"

# 测试端点：回环 9 端口（真实路径若被误用 → 连接立即拒绝，测试不悬挂）
TEST_MODEL_ENDPOINT = "https://127.0.0.1:9/v1/chat/completions"
TEST_MODEL_ENDPOINT_B = "https://127.0.0.1:10/v1/chat/completions"
TEST_MODEL_NAME = "hktax-test-model"
TEST_API_KEY = "sk-TEST-LOCAL-ONLY-1742"

# C3.6 工具注册表（封闭 4 工具；无 settings/update/rollback 工具）
REGISTRY_TOOL_NAMES = {
    "propose_input",
    "validate_input",
    "calculate_confirmed",
    "read_result",
}

# C3.4 六金额键（结构性核对；无金额数值期望）
AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)


# ---------------------------------------------------------------------------
# wire 响应构造（C3.6 上游 wire profile：choices[].message）
# ---------------------------------------------------------------------------
def wire_text_body(text: str) -> dict:
    """纯文本 assistant 响应（content 非空，无 tool_calls）。"""
    return {
        "status_code": 200,
        "body": json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": text}}]},
            ensure_ascii=False,
        ).encode("utf-8"),
    }


def wire_tool_calls_body(calls: list[dict], content=None) -> dict:
    """assistant tool_calls 响应；arguments 为 JSON 字符串（wire 层契约）。

    calls: [{"id", "name", "arguments": dict | str（str＝原样，可构造非法 JSON）}]
    """
    wire_calls = []
    for call in calls:
        args = call["arguments"]
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        wire_calls.append(
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": args},
            }
        )
    return {
        "status_code": 200,
        "body": json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": content,
                            "tool_calls": wire_calls,
                        }
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8"),
    }


# ---------------------------------------------------------------------------
# 假出站（C1：进程内实现，不增库；C4.6 ⑦段接缝）
# ---------------------------------------------------------------------------
class FakeModelOutbound:
    """进程内假出站：记录每次外发的确切冻结字节与密钥通道。

    - script: 逐次弹出的结果（dict=wire 响应；BaseException=传输级失败）；
      空则回落 default（200 纯文本）。
    - calls: 全部调用记录（keys: upstream_endpoint/api_key/frozen/kwargs）。
    """

    def __init__(self, script: list | None = None, default: dict | None = None):
        self.script = list(script or [])
        self.default = default if default is not None else wire_text_body(
            "好的（假出站默认回复）"
        )
        self.calls: list[dict] = []

    def send(self, upstream_endpoint, api_key, frozen, **kwargs):
        self.calls.append(
            {
                "upstream_endpoint": upstream_endpoint,
                "api_key": api_key,
                "frozen": frozen,
                "kwargs": dict(kwargs),
            }
        )
        item = self.script.pop(0) if self.script else dict(self.default)
        if isinstance(item, BaseException):
            raise item
        return dict(item)


class FakeStreamingResponse:
    """假 HTTP 响应：status_code + iter_bytes() 流式体 + close()（中止通道）。

    消费字节数逐块累计（1MB 流式累积上限断言用）。
    """

    def __init__(self, status_code: int, chunks):
        self.status_code = status_code
        self._chunks = chunks
        self.consumed = 0
        self.closed = False

    def iter_bytes(self):
        for chunk in self._chunks:
            self.consumed += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# AI 轮次助手（每轮＝新预览＋新授予＋chat：C4.6 每次外发独立授权）
# ---------------------------------------------------------------------------
def ai_preview(api, messages: list) -> dict:
    r = api.post("/api/v1/ai/consent/preview", json={"messages": messages})
    assert r.status_code in (200, 201), (
        f"AI 外发预览必须可用（§12.5/C4.5），实际 {r.status_code}: {r.text[:300]!r}"
    )
    return r.json()


def ai_grant(api, consent_id: str) -> dict:
    r = api.post("/api/v1/ai/consent", json={"consent_id": consent_id})
    assert r.status_code in (200, 201), (
        f"授予必须可用（C4.8 granted），实际 {r.status_code}: {r.text[:300]!r}"
    )
    return r.json()


def ai_chat(api, consent_id: str, messages: list):
    return api.post(
        "/api/v1/ai/chat", json={"consent_id": consent_id, "messages": messages}
    )


def ai_round(api, fake: FakeModelOutbound, messages: list, result) -> tuple:
    """一整轮外发（新授权）→ chat 响应＋预览负载。"""
    fake.script = [result]
    preview = ai_preview(api, messages)
    ai_grant(api, preview["consent_id"])
    return ai_chat(api, preview["consent_id"], messages), preview


def tool_results_of(payload: dict) -> list[dict]:
    """chat 响应的 tool_results（C3.6 工具层结果；缺失即明确失败）。"""
    results = payload.get("tool_results")
    assert isinstance(results, list) and results, (
        f"模型 tool_calls 轮必须在响应中回带 tool_results（C3.6 工具层），"
        f"实际负载：{json.dumps(payload, ensure_ascii=False)[:400]!r}"
    )
    return results


# ---------------------------------------------------------------------------
# 本地计算助手（与 tests/ui/_helpers 同构的瘦身版；AI 车道独立维护）
# ---------------------------------------------------------------------------
SALARIES_EXECUTE_TARGET = "/api/v1/calc/salaries-tax"


def salaries_input(**overrides) -> dict:
    inp = {
        "year_of_assessment": "2025_26",
        "employment_income": "240000",
        "mpf_mandatory_contributions": "9000",
        "married_status": {"value": "single"},
    }
    for key, value in overrides.items():
        if value is ...:
            inp.pop(key, None)
        else:
            inp[key] = value
    return inp


def _v2_year_block(start: int, marriage_value: str, finance: dict) -> dict:
    return {
        "participants": [{"person_id": "p1", "role": "self"}],
        "subject_facts": {},
        "household": {
            "marriage": [
                {
                    "from": f"{start}-04-01",
                    "to": f"{start + 1}-04-01",
                    "value": marriage_value,
                }
            ],
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        "relations": [],
        "care_payments": [],
        "claims": [],
        "financials": {"p1": copy.deepcopy(finance)},
        "assessment": {
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
        },
        "manual_issues": [],
    }


def _v2_from_v1(inp: dict) -> dict:
    """v1 事实模型（AI 测试输入）→ Annex 005 v2 最小 Year 容器。

    显式已缴暂缴时同时给出紧邻下一年 Year 容器（v2 complete 需
    ``next_provisional``；仅目标年时为 partial）。
    """
    year = inp["year_of_assessment"]
    start = int(year[:4])
    finance = {
        "has_assessable_salary_income": True,
        "source_classification": {"salary": "confirmed_in_scope"},
        "properties": [],
        "businesses": [],
        "deduction_items": [],
    }
    for key in ("employment_income", "mpf_mandatory_contributions"):
        if key in inp:
            target = "employment_income_nai" if key == "employment_income" else key
            finance[target] = inp[key]
    if "deduction_items" in inp:
        finance["deduction_items"] = inp["deduction_items"]
    status = inp["married_status"]["value"]
    marriage_value = "unmarried" if status == "single" else status
    year_block = _v2_year_block(start, marriage_value, finance)
    years = {year: year_block}
    if "provisional_paid" in inp:
        year_block["provisional_paid"] = inp["provisional_paid"]
        following = f"{start + 1}_{(start + 2) % 100:02d}"
        if following in ("2024_25", "2025_26", "2026_27"):
            years[following] = _v2_year_block(start + 1, marriage_value, finance)
    return {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": years,
        "hk_presence_days": [],
        "assumptions": [],
    }


def fresh_if_match(api) -> dict[str, str]:
    """S002 §4.1：每次 prepare 前重取 ``"edit-<epoch>-<revision>"``。"""
    r = api.get("/api/v1/meta/version")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "session_epoch" in payload and "input_revision" in payload, (
        "S002 §4.1：meta/version 须附运行时 session_epoch/input_revision，"
        f"实际：{payload!r}"
    )
    return {
        "If-Match": (
            f'"edit-{int(payload["session_epoch"])}-{int(payload["input_revision"])}"'
        )
    }


def prepare_body(inp: dict | None = None) -> dict:
    """薪俸 prepare 信封：v2 input＋schema 2.0.0（S002 ADR-3）；v1 形态自动镜像。"""
    source = inp or salaries_input()
    canonical = source if "years" in source else _v2_from_v1(source)
    return {"tax_type": "salaries_tax", "schema_version": "2.0.0", "input": canonical}


def run_prepare(api, inp: dict | None = None) -> dict:
    """prepare → 201（v2 信封＋新鲜 If-Match；S002 ADR-3/§4.1）。"""
    r = api.post(
        "/api/v1/calc/prepare",
        json=prepare_body(inp),
        headers=fresh_if_match(api),
    )
    assert r.status_code == 201, f"prepare 应 201（C3.2.1），实际 {r.status_code}: {r.text}"
    return r.json()


def run_confirm(api, prepared: dict) -> str:
    r = api.post(
        "/api/v1/calc/confirm",
        json={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": True,
        },
    )
    assert r.status_code == 200, f"confirm 应 200（C3.2.2），实际 {r.status_code}: {r.text}"
    return r.json()["confirmation_id"]


def complete_calc(api, inp: dict | None = None) -> dict:
    """完整三段流程 → {prepare, confirmation_id, response, record_id}。"""
    prepared = run_prepare(api, inp)
    confirmation_id = run_confirm(api, prepared)
    response = api.post(SALARIES_EXECUTE_TARGET, json={"confirmation_id": confirmation_id})
    assert response.status_code == 200, (
        f"execute 应 200（C3.2.3），实际 {response.status_code}: {response.text[:300]!r}"
    )
    payload = response.json()
    return {
        "prepare": prepared,
        "confirmation_id": confirmation_id,
        "response": payload,
        "record_id": payload.get("record_id"),
    }


def publish_alt_bundle(store) -> dict:
    """发布第二份有效规则束（C3.2.4/C4.6 rules 变化触发器）。"""
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    content = copy.deepcopy(INITIAL_BUNDLE_CONTENT)
    digests = list(content.get("evidence_digests", []))
    digests.append("sha256:" + "ab" * 32)
    content["evidence_digests"] = digests
    return store.publish(content, evidence_refs=[])
