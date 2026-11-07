# HK Tax Calculating Agent（香港税务计算 AI Agent）

本地、确定性、单进程的香港税务／印花税估算工具（FastAPI ＋ Pydantic v2 ＋ Uvicorn ＋ Jinja2 SSR）。

- 设计规格：`specs/001-hk-tax-agent.md`（Implemented）；`specs/002-tax-correctness-remediation.md`（Implemented（stage1）；阶段一）
- 技术契约：`docs/design/002-hk-tax-technical-contract.md`（Annex C）
- 税法语义：`docs/design/003-hk-tax-legal-contract.md`（Annex D）
- 002 事实契约：`docs/design/005-tax-correctness-fact-contract.md`（Annex 005，Implemented（stage1））
- 002 验证附件：`docs/design/006-tax-correctness-verification.md`（Implemented（stage1））
- 002 UI delta：`docs/design/004-tax-correctness-ui.md`（Implemented（stage1））

## 免责声明（Disclaimer）

- 本项目为独立开源工具，与香港税务局（IRD）**不隶属**；**不宣称**获得任何官方认证或认可，**不获税务局认证**（not endorsed / unofficial / not affiliated with the IRD）。
- 所有计算结果仅为估算，不构成税务意见或法律意见；正式评税以香港税务局为准。

## 002 实施状态（阶段一）

- 版本：engine `0.2.0`；规则束 `rules_schema_version 2.0.0`；薪俸／个人入息课税 input `2.0.0`，其余五税 input 维持 `1.0.0`。
- 状态：002 SDD／Annex 005／006／004 均为 **Implemented（stage1）**；P1／P2／P3 已完成；P3 final5 oracle PASS＋acceptor ACCEPTED（2026-10-20）；历史 OPEN 边界保留（45-red、W1b 原始写前字节、F3 全量、pre-seal、21 项 history_unavailable、视觉 B/D/F、stage2/3 未扩）；验收不构成完整历史证明。
- 本次 W2 同步：根全量 pytest（含真实 Chromium e2e）、002 追溯命名空间、离线固定束报告与文档/版本对齐；视觉证据已截取供 designer 复核，**不构成视觉 PASS**。
- 2026-10-20：P3 final5 oracle PASS＋acceptor ACCEPTED 后，SDD/Annex 005/006/004 状态更新为 Implemented（stage1）；仅文档状态/日期/关闭记录变更，代码/测试/脚本/规范语义未变。
- 002 追溯：`.venv\Scripts\python scripts\check_req_traceability.py --spec 002 --require-002 1,2,3,4,5,6,7,8,9,10,11,12,13`（001 的 `--json`/`--require` 接口保持不变）。
- 固定束离线核验：`.venv\Scripts\python -m scripts.official_calc_compare.run_all --report <EV>\reports\002-fixed-bundle.md`；默认不访问网络，输出明确标注「固定束核验/模拟，不代表实时规则可用性」，exit 0 不等于实时正确性。`--live` 才显式驱动官方计算器。
- 001 报告脚本保持历史路径；002 报告必须用 `--owner 002 --output <EV>\reports\...`，脚本拒绝覆盖 `docs/validation/001-engine-io-report.md`。

## 环境要求

- Python **3.12 / 3.13**（获批解释器；更高版本超出基线，勿用默认解释器直接建 venv）
- Windows 10/11 为基线运行环境

## 安装

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python --version        # 必须输出 3.12.x / 3.13.x，否则删除重建
.venv\Scripts\pip install -e ".[dev]"
playwright install chromium           # 仅 e2e 测试需要
```

## 启动

```powershell
.venv\Scripts\python -m app.main            # 受控入口：默认 127.0.0.1:8000
# 或：powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1 -Port 8000（先加载 .env.local，再走同一受控入口）
```

受控入口（`app.main.run()`，经 `python -m app.main` 调用）强制：仅回环绑定（`127.0.0.1` / `localhost`，其他 `--host` 拒绝启动）、单 worker、不信任代理头。直接 `uvicorn app.main:app` CLI 虽可运行，但会绕过上述校验，**不是受支持路径**；本服务无身份认证，任何非回环暴露都等于未授权开放全部 API（含服务端模型密钥的使用）。

## 模型（AI）配置与禁用

- **配置**：三个服务端环境变量（Annex C C4.2）——
  - `HKTAX_MODEL_ENDPOINT`：上游模型端点（非秘密；OpenAI 兼容 Chat Completions）；
  - `HKTAX_MODEL_NAME`：模型名称（非秘密）；
  - `HKTAX_MODEL_API_KEY`：模型密钥（仅存服务端，绝不进入前端、页面或日志）。
- **未配置/禁用**：不设置 `HKTAX_MODEL_API_KEY` 即为纯本地模式——本地表单计算完全不依赖模型，AI 外发授权（consent）可随时撤回；缺少配置时 AI 功能返回 `E_MODEL_UNCONFIGURED`（本地计算照常可用）。
- **数据去向**：启用 AI 时，外发 payload 将发送至你自行配置的模型供应商（可能发生跨境传输）；供应商侧条款与数据处理政策由你自行评估，缺失信任时请使用纯本地模式。详见 [LEGAL.md](LEGAL.md) §2.4。

## 支持期间

- 直接税（薪俸税／利得税／物业税／个人入息课税评税选择）：2024/25、2025/26、2026/27（2026/27 为未完结年度，按已确认规则快照计算并提示）。
- 印花税文书日期初始窗：2024-04-01 至 2026-10-07。

## 界面页面

- 计算工作台 `/`、税项覆盖 `/coverage`；税项页 `/salaries` `/profits` `/property` `/personal-assessment` `/stamps/property` `/stamps/stock` `/stamps/lease`；信息页 `/rules` `/updates` `/settings` `/about`。

## 维护

- 运行测试：`.venv\Scripts\python -m pytest`（根目录全量，含 `tests/acceptance`、真实 Chromium `tests/e2e`、`tests/hypothesis` 与根测试）
- 需求追溯检查：`.venv\Scripts\python scripts\check_req_traceability.py`（001；002 用 `--spec 002 --require-002 ...`）
- 持续集成：GitHub Actions（`.github/workflows/ci.yml`）在 push／PR 时以 Python 3.12、3.13 跑全量测试（含 Chromium e2e）；push 与手动触发时另跑 001 验证报告（门禁）＋002 官方计算器对比（**默认离线固定束核验；live 需显式 `--live` opt-in**），并上传 `docs/validation/` 工件（报告＋截图）。
- 数据库：运行时默认 `%LOCALAPPDATA%\hktax-agent\rules.db`（可用 `HKTAX_DB_PATH` 覆盖；仓库内无 DB）。
- 规则更新节奏（与 `app/updater/scheduler` 实现一致）：
  - **首启即查**：首次启动（无成功记录）时调度器 `startup` 立即生成检查作业；逾期未查则启动时单次合并补查。
  - **按月自动**：以上一次完整成功的实际完成日为锚，按日历月计划下一次检查（月末截断；失败有界退避）。
  - **手动检查**：`POST /api/v1/update/check`「立即检查」与计划检查走同一条管线（进程内 singleflight，幂等）。
  - 检查成功 ≠ 规则已更新：候选须通过独立验证与门禁才在单事务内原子发布；官方来源标注失效/已知变更的受影响期间拒绝计算，离线时已核验快照可计算并附中文提示。检查不发送任何用户财务数据、不依赖模型。
- 当前束已入数数值均挂 T1 台账锚点（`docs/research/001-hk-tax-rule-evidence.md`；见束 `evidence_digests`）。

## 依赖与许可

| 依赖 | 类型 | 许可（按各包实际声明；以其包元数据为准） |
| --- | --- | --- |
| [fastapi](https://pypi.org/project/fastapi/) | 运行时 | MIT |
| [jinja2](https://pypi.org/project/jinja2/) | 运行时 | BSD-3-Clause |
| [pydantic](https://pypi.org/project/pydantic/) | 运行时 | MIT |
| [uvicorn](https://pypi.org/project/uvicorn/) | 运行时 | BSD-3-Clause |
| [httpx](https://pypi.org/project/httpx/) | 运行时 | BSD-3-Clause |
| [pytest](https://pypi.org/project/pytest/) | dev | MIT |
| [hypothesis](https://pypi.org/project/hypothesis/) | dev | MPL-2.0 |
| [playwright](https://pypi.org/project/playwright/)（Python 绑定；浏览器内核另有其自身许可） | dev | Apache-2.0 |

- 上表按各依赖包当前公开元数据整理；版本区间以 `pyproject.toml` 为准，升级后请以安装包元数据复核许可。解析类依赖（beautifulsoup4/pypdf）按用户决定不引入。
- 参考/重用项目：[leeyc0/hksalariestax](https://github.com/leeyc0/hksalariestax)（固定 commit `94e4ddca53d792316e6a53b2fb2fa8cc9e571a91`），MIT License，Copyright (c) 2018 leeyc0；保留其版权与许可记录。
- 本项目许可：[LICENSE](LICENSE)（MIT）；合规说明与免责声明：[LEGAL.md](LEGAL.md)；第三方通知（含依赖许可与官方资料引用说明）：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
