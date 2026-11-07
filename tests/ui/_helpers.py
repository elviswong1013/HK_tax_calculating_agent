"""M6 UI 测试共享助手（非测试模块；供 tests/ui 各文件 `from _helpers import ...`）。

内容：
  - 薪俸税事实输入构造（金额一律 AmountStr 字符串；PAM39 Q1 输入事实仅作载荷，
    不作任何税额期望——同 tests/api 既有口径）。
  - prepare→confirm→execute 全流程驱动（C3.2 三段）。
  - 服务端接缝注入：向当前会话注入记录（打印渲染测试）；发布第二规则束
    （rules 变化触发器，Annex A 12.2/C3.2.4）。
  - 工作台 SSR HTML 结构审计（stdlib HTMLParser；不新增解析依赖——C1 门禁）。
"""

from __future__ import annotations

import copy
import json
import re
from html.parser import HTMLParser

SALARIES_EXECUTE_TARGET = "/api/v1/calc/salaries-tax"

# C3.4 六金额键（结构性核对用；无金额数值期望）
AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)

# Annex A §12.2：重放历史记录的权威中文标示
REPLAY_MARKER = "历史估算，非当前重新评估"


def salaries_input(**overrides) -> dict:
    """完整合法的薪俸税事实输入；overrides 值为 ... 时删除键。"""
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


def _year_block(start: int, marriage_value: str, finance: dict) -> dict:
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
    """v1 事实模型（UI 测试输入）→ Annex 005 v2 最小 Year 容器。

    显式已缴暂缴（会要求 complete 的节点）同时给出紧邻下一年 Year 容器：
    v2 的 complete 需 ``next_provisional``（005 §8）；仅目标年时为 partial。
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
    year_block = _year_block(start, marriage_value, finance)
    years = {year: year_block}
    if "provisional_paid" in inp:
        year_block["provisional_paid"] = inp["provisional_paid"]
        following = f"{start + 1}_{(start + 2) % 100:02d}"
        if following in ("2024_25", "2025_26", "2026_27"):
            years[following] = _year_block(start + 1, marriage_value, finance)
    return {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": years,
        "hk_presence_days": [],
        "assumptions": [],
    }


def prepare_body(inp: dict) -> dict:
    """薪俸 prepare 信封：v2 input＋schema 2.0.0（S002 ADR-3）；v1 形态自动镜像。"""
    canonical = inp if "years" in inp else _v2_from_v1(inp)
    return {"tax_type": "salaries_tax", "schema_version": "2.0.0", "input": canonical}


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


def run_prepare(api, inp: dict | None = None) -> dict:
    """prepare → 201；返回负载（含 prepared_id／input_hash／expected_binding）。"""
    r = api.post(
        "/api/v1/calc/prepare",
        json=prepare_body(inp or salaries_input()),
        headers=fresh_if_match(api),
    )
    assert r.status_code == 201, f"prepare 应 201（C3.2.1），实际 {r.status_code}: {r.text}"
    return r.json()


def run_confirm(api, prepared: dict) -> str:
    """对 prepared 显式确认（acknowledge=true）→ confirmation_id。"""
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


def run_execute(api, confirmation_id: str, target: str = SALARIES_EXECUTE_TARGET):
    """向确认能力绑定的执行目标提交（C3.2.3）。"""
    return api.post(target, json={"confirmation_id": confirmation_id})


def complete_calc(api, inp: dict | None = None) -> dict:
    """完整三段流程；返回 {prepare, confirmation_id, response, record_id, binding}。

    Red 阶段（引擎未接入 execute）response.status 为 blocked —— 各测试按其
    断言自行判定期望（本助手不做 status 断言）。
    """
    prepared = run_prepare(api, inp)
    confirmation_id = run_confirm(api, prepared)
    response = run_execute(api, confirmation_id)
    assert response.status_code == 200, (
        f"execute 应 200（C3.2.3），实际 {response.status_code}: {response.text}"
    )
    payload = response.json()
    return {
        "prepare": prepared,
        "confirmation_id": confirmation_id,
        "response": payload,
        "record_id": payload.get("record_id"),
        "binding": prepared["expected_binding"],
    }


def inject_record(harness, core: dict) -> str:
    """向当前测试会话注入一条记录（打印/记录渲染的服务端接缝）→ record_id。"""
    sid = harness.session_id()
    record_id = "r-inject-" + core.get("tax_type", "x")
    harness.registry.save_record(sid, {"record_id": record_id, "core": core})
    return record_id


def publish_alt_bundle(store) -> dict:
    """发布第二份有效规则束（rules 变化触发器；原 bundle 仍留在库中可按 id 载入）。

    深拷贝初始束并追加一条 evidence digest —— 结构合法但内容哈希不同，
    指针切换到新 bundle（C3.2.4 rules 变化行）。
    """
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    content = copy.deepcopy(INITIAL_BUNDLE_CONTENT)
    digests = list(content.get("evidence_digests", []))
    digests.append("sha256:" + "ab" * 32)
    content["evidence_digests"] = digests
    return store.publish(content, evidence_refs=[])


def binding_four_keys(binding: dict) -> bool:
    """C3.2.1：完整四元组 binding。"""
    return set(binding.keys()) == {
        "bundle_id",
        "bundle_hash",
        "rules_schema_version",
        "engine_version",
    }


# ---------------------------------------------------------------------------
# 工作台 SSR HTML 结构审计（stdlib；Annex A §3/§4/§6/§9 结构契约）
# ---------------------------------------------------------------------------

class WorkbenchAudit(HTMLParser):
    """收集表单控件／label 关联／样式与脚本资产引用。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[dict] = []      # 需程序关联 label 的控件
        self.label_for: set[str] = set()    # <label for=...> 目标集合
        self.inline_styles: list[str] = []  # <style> 文本
        self.css_links: list[str] = []      # <link rel=stylesheet href>
        self.script_srcs: list[str] = []    # <script src=...>
        self.inline_scripts: list[str] = []  # 内联脚本文本
        self.button_count = 0
        self._style_depth = 0
        self._style_buf: list[str] = []
        self._script_depth = 0
        self._script_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        a = dict(attrs)
        if tag in ("input", "select", "textarea"):
            input_type = (a.get("type") or "text").lower()
            if input_type not in ("hidden", "submit", "button", "reset"):
                self.controls.append(
                    {
                        "tag": tag,
                        "type": input_type,
                        "id": a.get("id") or "",
                        "aria_label": a.get("aria-label") or a.get("aria-labelledby") or "",
                        "name": a.get("name") or "",
                    }
                )
        elif tag == "label":
            if a.get("for"):
                self.label_for.add(a["for"])
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            if "stylesheet" in rel and a.get("href"):
                self.css_links.append(a["href"])
        elif tag == "style":
            self._style_depth += 1
        elif tag == "script":
            if a.get("src"):
                self.script_srcs.append(a["src"])
            else:
                self._script_depth += 1
        elif tag == "button":
            self.button_count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self._style_depth:
            self._style_depth -= 1
            self.inline_styles.append("".join(self._style_buf))
            self._style_buf = []
        elif tag == "script" and self._script_depth:
            self._script_depth -= 1
            self.inline_scripts.append("".join(self._script_buf))
            self._script_buf = []

    def handle_data(self, data: str) -> None:
        if self._style_depth or self._script_depth:
            self._style_buf.append(data)
            self._script_buf.append(data)


def audit_workbench(html: str) -> WorkbenchAudit:
    parser = WorkbenchAudit()
    parser.feed(html)
    parser.close()
    return parser


def strip_media_blocks(css: str) -> str:
    """删除 @media {...} 块（断点条件与桌面段内的宽度声明不构成移动横向滚动）。"""
    out: list[str] = []
    i = 0
    while True:
        m = re.search(r"@media[^{]*\{", css[i:])
        if not m:
            out.append(css[i:])
            break
        start = i + m.start()
        out.append(css[i:start])
        depth = 1
        j = i + m.end()
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


def fixed_width_violations(css: str, limit_px: int = 375) -> list[str]:
    """@media 之外的固定 min-width/width 像素声明（> limit_px 即必需横向滚动风险）。"""
    base = strip_media_blocks(css)
    hits: list[str] = []
    for m in re.finditer(r"(?<![\w-])(?:min-)?width\s*:\s*(\d+)\s*px", base):
        if int(m.group(1)) > limit_px:
            hits.append(m.group(0))
    return hits


def fetch_asset(api, href: str):
    """取同站资产（以 / 开头的相对链接）；跨站引用不属于本应用可控资产。"""
    if not href.startswith("/"):
        return None
    return api.get(href)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
