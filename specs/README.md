# 规格索引与开发门禁

Updated: 2026-10-09

本仓库采用 PRD → SDD → TDD 的门禁式流程。初始化检查时，仓库没有已有规范目录、应用代码或测试框架。

## 文档位置

- PRD：`docs/prd/NNN-short-name.md`。
- SDD：`specs/NNN-short-name.md`；使用三位编号及短英文主题名。
- SDD 模板：`specs/_template.md`（已于 2026-10-07 建立）；不得以模板代替具体规格的审批。
- 审计证据：`docs/reviews/`；审计记录不能批准需求。
- 文档正文默认使用中文，状态及日期字段保持统一。

## 门禁

1. **PRD**：记录背景、目标、非目标、用户故事、编号验收条件 `AC-1..N`、约束及待确认项。初始 `Status: Draft`；必须获得用户明确批准后才进入 SDD。
2. **SDD**：基于已批准 PRD 和真实代码库编写；包含编号需求 `REQ-1..N`、AC → REQ 映射、所需设计、错误路径、任务拆分及覆盖每项 REQ 的命名测试计划。初始 `Status: Draft`。
3. **规格审批**：`@plan-reviewer` 必须返回 `APPROVED`，用户随后明确批准，才能设置 `Status: Approved`。高风险或跨系统规格还需并行咨询 `@councillor-glm`、`@councillor-kimi`、`@councillor-astra`，再由 `@council` 汇总；圆桌不能替代任一审批门禁。
4. **TDD**：每项任务先由 `@test-writer` 编写测试并确认因预期原因失败，再由 `@executor` 最小实现；主执行者不可用时使用 `@executor-backup`。界面设计由 `@designer` 负责，界面实现可由 `@frontend-executor` 在既定设计和 Red 测试基础上完成。最后在测试保持通过的前提下重构。不得为通过测试而弱化、跳过或删除测试；错误预期属于规格问题，须停止并报告。
5. **最终验收**：所有写入任务结束并协调后，运行完整测试套件，提供变更到 REQ 的追踪证据，由只读 `@acceptor` 返回 `ACCEPTED`。之后才设置 `Status: Implemented` 并更新 `Updated:`。

批准前不得编写生产代码，也不得提前执行下一阶段。各里程碑的 TDD 循环不需中途再次签字，但范围变更、规格冲突和缺失门禁必须报告。**技术预审（pre-review）只覆盖技术契约范围，不构成 @plan-reviewer 全局 APPROVED**；索引须持续记录三项待决：①复审与批准：**@plan-reviewer 第六轮（2026-10-08）APPROVED**（设计合同可执行性；不证明产品取整约定等同 IRD 实际评税）；**用户 Gate2 已批准（2026-10-08，含产品精度约定确认项；授权按 Annex C C1 顺序安装依赖）**；②解析依赖——用户已于 2026-10-07 答复「**暂不增加依赖**」，`beautifulsoup4`／`pypdf` 等未获批准、**不得安装**，也不得以其他方案绕过同一 OPEN gate（Annex C C1/C7.3）；③R1–R5 税法**子项闭合中——Cap.112/Cap.117 现行法例文本与官方计算器行为已并入 [Annex D](../docs/design/003-hk-tax-legal-contract.md)（Draft），Gate2 阻断清单以 **Annex D D10 为唯一权威**：薪俸/PA/非法团利得取整＝三年度官方计算器 floor 观察已闭；**法团/混合最终取整＝产品估算精度约定（floor；标注未经 IRD 文字证明，列为用户 Gate2 确认项，fail-closed guard 保留）**；物业评税/聚合单位已冻结（观察＝输入聚合字段、推导＝BIR57 逐项物业，分层标注）——已随第六轮复审闭合；产品精度约定经用户 2026-10-08 Gate2 确认**＋范围外注记（s.45/s.29D(3)；Ord 8/2024 已并入现行文本、Ord 5/2026 现行整合本无注记（索引有 PDF、正文未核）＝范围外/guard）；**s.12B 已闭；s.60＝评税程序、首版不模拟追加评税（s.43(2B) 归原所得人说明引用保留）；joint PA 首版输出规则已定**（权威金额＝宽减后 joint 总税额、个人份额保留精确 Fraction、不输出个人整元分摊税额、ΣR＝0 无税不分摊）；**R1–R5 文档级语义已随第六轮复审与 Gate2 闭合并实现**（相反官方证据 fail-closed guard 保留）。

## 状态与变更

- SDD 状态：`Draft` → `Approved` → `Implemented` → 可选 `Superseded`。
- PRD 使用 `Draft`、`Approved`；PRD 批准不代表实现完成。
- 文档包含 `Created:` 和 `Updated:`，日期格式 `YYYY-MM-DD`。
- 修改 `Approved` 或 `Implemented` 规格是变更请求；重新执行受影响的审批及验证门禁，同步代码和测试后才继续其他工作。
- 保留已实现及被替代的规格作为审计记录；被替代文件注明替代关系与状态。

## 规格索引

| 编号 | 主题 | PRD | SDD | 当前阶段 |
| --- | --- | --- | --- | --- |
| 001 | 香港税务计算 AI Agent 与交互界面 | [PRD](../docs/prd/001-hk-tax-agent.md) | [SDD（Implemented）](001-hk-tax-agent.md) | SDD **Implemented**（2026-10-09 第三轮独立验收 **ACCEPTED**——验收由用户指定 GLM-5.3 承载；全量 157 tests passed＋追溯 18/18；2026-10-08 @plan-reviewer 第六轮 APPROVED＋用户 Gate2 批准（含产品精度约定确认项）；[Annex C](../docs/design/002-hk-tax-technical-contract.md) 技术契约权威；税法语义以 [Annex D](../docs/design/003-hk-tax-legal-contract.md)（Approved）为权威——Cap.112/Cap.117 法定基础与三年度官方计算器行为已并入；Gate2 确认项以 Annex D D10 为唯一权威（法团/混合最终取整＝产品精度约定**已经用户 2026-10-08 Gate2 确认**——标注未经 IRD 文字证明、fail-closed guard 保留；物业单位已冻结；s.12B 已闭、s.60 首版不模拟追加评税、joint PA 首版输出规则已定）；HTML/PDF 解析依赖：用户 2026-10-07 答复「暂不增加依赖」——未批准、不得安装、不得绕道，选型 OPEN） |
