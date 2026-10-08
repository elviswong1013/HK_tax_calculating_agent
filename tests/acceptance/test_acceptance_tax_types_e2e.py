"""REQ-4/5/6/7/12 六个已批准税项端到端可用性＋计算页路由（终验 REJECTED 缺口 Red）。

REQ: REQ-4（利得税）／REQ-5（物业税）／REQ-6（个人入息课税评税选择）／
REQ-7（印花税：物业 AVD＋股票＋租约）＋REQ-12（SSR 计算页可达）。
验收发现锚点（终验 REJECTED）:
  - app/api/validation.py 仅放行 salaries_tax——其余税项 prepare 即
    E_INPUT_TYPE「M1 仅支持 salaries_tax」；app/web/records.run_engine 对非
    薪俸税项同样显式拒绝。六个已批准税项无一走通 prepare→confirm→execute。
  - 页面路由仅 / 与 /coverage 存在；/salaries /profits /property
    /personal-assessment /stamps/* /rules /updates /settings /about 均 404。
规格锚点:
  - Annex C C3.1 端点表（7 个执行目标端点）＋C3.2 三段（prepare 201／
    confirm 200＋confirmation_id／execute 200＋record_id）＋C3.5 各税项事实
    字段（事实构造与 tests/engines/* 既绿契约同形）。
  - SDD §4 页面路由：/salaries /profits /property /personal-assessment
    /stamps*（property/stock/lease）＋/rules /updates /settings /about。
期望值来源: 结构/状态断言（201/200/200、status=complete、record_id、
  页面 200＋税项内容关键词）；最小合法事实只要求结构性完成，无金额数值期望。
"""

from __future__ import annotations

from _acc_helpers import TAX_EXECUTE_TARGETS, confirm, execute, minimal_facts, prepare

# 页面路由 → 必含的税项/页面内容关键词（REQ-12；页面须含对应税项内容）
PAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/salaries", ("薪俸税",)),
    ("/profits", ("利得税",)),
    ("/property", ("物业税",)),
    ("/personal-assessment", ("个人入息课税",)),
    ("/stamps/property", ("印花税", "物业")),
    ("/stamps/stock", ("印花税", "股票")),
    ("/stamps/lease", ("印花税", "租约")),
    ("/rules", ("规则",)),
    ("/updates", ("更新",)),
    ("/settings", ("设置",)),
    ("/about", ("关于",)),
)


def test_acceptance_all_approved_tax_types_end_to_end(api) -> None:
    """六个已批准税项（7 个执行目标）各自 prepare→confirm→execute 全链路成功
    （最小合法事实，status=complete＋record_id，不得 E_INPUT_TYPE/404）；
    计算页/信息页路由全部 200 且含对应税项内容。"""
    problems: list[str] = []

    # —— 阶段一：7 个执行目标的三段链路（C3.2）——
    for tax_type, target in TAX_EXECUTE_TARGETS.items():
        facts = minimal_facts(tax_type)
        try:
            r_prepare = prepare(api, tax_type, facts)
            if r_prepare.status_code != 201:
                problems.append(
                    f"{tax_type}: prepare 须 201（最小合法事实），实际 "
                    f"{r_prepare.status_code} {r_prepare.text[:200]!r}"
                )
                continue
            prepared = r_prepare.json()
            if not prepared.get("prepared_id"):
                problems.append(f"{tax_type}: prepare 201 但缺 prepared_id")
                continue
            r_confirm = confirm(api, prepared)
            if r_confirm.status_code != 200:
                problems.append(
                    f"{tax_type}: confirm 须 200，实际 {r_confirm.status_code} "
                    f"{r_confirm.text[:200]!r}"
                )
                continue
            confirmation_id = r_confirm.json().get("confirmation_id")
            r_execute = execute(api, confirmation_id, target)
            if r_execute.status_code != 200:
                problems.append(
                    f"{tax_type}: execute {target} 须 200，实际 "
                    f"{r_execute.status_code} {r_execute.text[:200]!r}"
                )
                continue
            payload = r_execute.json()
            if payload.get("status") != "complete":
                problems.append(
                    f"{tax_type}: 最小合法事实须 status=complete，实际 "
                    f"{payload.get('status')!r}（missing={payload.get('missing_components')!r}）"
                )
            if not payload.get("record_id"):
                problems.append(f"{tax_type}: execute 200 但缺 record_id")
        except Exception as exc:  # 结构意外（非断言失败）也计入缺口清单
            problems.append(f"{tax_type}: {type(exc).__name__}: {exc}")

    # —— 阶段二：页面路由可达＋含对应税项内容（REQ-12）——
    for path, keywords in PAGES:
        r = api.get(path)
        if r.status_code != 200:
            problems.append(
                f"GET {path} 须 200（SDD §4 计算页/信息页路由），实际 {r.status_code}"
            )
            continue
        for keyword in keywords:
            if keyword not in r.text:
                problems.append(
                    f"GET {path} 200 但缺对应税项内容关键词 {keyword!r}"
                )

    assert not problems, (
        "终验缺口：六个已批准税项未全部端到端可用／页面路由缺失：\n  - "
        + "\n  - ".join(problems)
    )
