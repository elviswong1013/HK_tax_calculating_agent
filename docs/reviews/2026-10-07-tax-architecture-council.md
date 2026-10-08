# 2026-10-07 香港税务架构 Council 评审记录（Review Record / Annex）

Status: Review record only — **不是批准、不是验收、不是正式 plan-reviewer 意见、不批准任何 REQ**
Created: 2026-10-07
Updated: 2026-10-07
Inputs: glm（GLM-5.3 max）、kimi（Kimi K3 1M max）、astra（GPT-6 Astra medium）——三名输入均回复，无失败。
Refs: `docs/prd/001-hk-tax-agent.md`（Approved，AC-1..AC-18）；`docs/design/001-hk-tax-ui.md`（Draft，UI/UX 提案）；`docs/research/001-hk-tax-rule-evidence.md`（证据台账，T1/T2/T3/blocked）；`specs/001-hk-tax-agent.md`（计划路径；主 SDD 编写中，council **未读**）。

> 本记录只汇总 council 输入与合成，供 orchestrator 事实审计与 plan-reviewer 参考，不构成 SDD 批准、实现授权或 ACCEPTED 结论。来源/日期锚点保持精炼，不整段转述意见。

## 1. 合成结论（Synthesis）

- **DESIGN DIRECTION SUPPORTED / NOT READY FOR FINAL SDD APPROVAL**。
- 评审基于**初步 PRD/UI 架构**；**未读取主 SDD**，不是正式 plan-reviewer 评审。正式评审尚未进行，不预设其 verdict。
- 后续 oracle 澄清：完整未来 fixture **不是** SDD 的前置条件（不构成计算分歧）；必要计算/验证语义仍须按台账 7.1 在 Gate 2 前闭合。

## 2. 共识基线（Consensus）

- 本地确定性税务计算核心（精确 Decimal 金额）；本地 Web；AI 可选且不决定税额；SQLite 保存不可变规则、证据、发布和调度记录，不默认保存财务会话；规则数据经验证自动发布；精确外发预览；服务端密钥（对齐 PRD AC-14）。

## 3. MUST 契约（合并输入）

- **语义与覆盖**：按税种/年度/主体维护计算合同与法律适用矩阵；区分法定依据与观察到的官方估算器取整行为；比较宽减后的 PA 税额；**普通合伙 PA 与普通（非出售）股票转让必须保留**，不得静默移除。
- **独立预期值**：独立 oracle 方法与命名测试；详见台账 7.1；不要求在 SDD 阶段预先列完每个未来边界测试的具体预期值。
- **DATA-only**：冻结 engine + 测试 refs + 完整来源证据。
- **来源状态**：candidate vs quarantine（active / future / expired / offline / known-changed）。
- **调度**：每月 Asia/Hong Kong anchor；保留月末；1 月/2 月 clamp → 3 月 31 日；全局 full success；retry/attempt/manual coalesce。
- **记录与重放**：financial records 临时保存 + export；engine bundle version replay 与“按当前规则重算”区分。
- **外发安全**：精确序列化 payload；endpoint 单次 consent、timeout retry；SDK 不 append；revision 使 late-response 失效；loopback Host/Origin/CSRF、无 CORS。

## 4. 事实分歧与更正（已由输入纠正）

- kimi 称 “2026 income allowances proposed” 错误：预算页措施 (i)–(iv) 已于 **2026-05-13 通过、2026-05-22 刊宪**；页面部分印花税段保留提案措辞，属另一事项，不可推及已通过的所得税措施。
- 2026 FAQ 的 160,000（次名、2026-09-16 起）**未确认为法**；不得自动视为已生效，也不得简化为单一 140k 替换。
- 双源政策**不是**机械“两个网站”：需要来源权威 + 独立 oracle；保存完整 bounded snapshots，不只 hash 链接。

## 5. 范围与持续阻塞

- Council 建议**不做强制范围削减**；以门禁推进（no mandatory scope cut; gated progress）。
- 完整英文界面、额外 PDF 美化、CLI 等可选改进不因此加入已批准范围。
- 持续阻塞：利得税/物业税最终取整仍 UNVERIFIED（台账 B5）；PA 普通模型/分配与 11:00 时间切点等见台账 B11/B12。

## 6. 审计说明

- 本文件仅证据记录，**不批准任何 REQ**；未运行测试、未通过任何门禁；不宣称 ACCEPTED / Implemented。
- 后续事实审计归 orchestrator；正式计划评审归 plan-reviewer（须在其读取主 SDD 后进行）。
