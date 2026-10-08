"""更新管线（M5；REQ-17/18；SDD §5 调度/更新管线行＋Annex C C5.6/C6/C7/C8）。

模块边界（SDD §5 管线七阶段，每阶段产物可审计）：
- fetcher    allowlist 出站封装（全文有界快照 ≤10MB）；
- archive    快照归档（hash 去重、引用 pin、每 URL 24 份修剪，C7.8）；
- parser     manifest 封闭 schema 校验＋锚定 DOM 有界抽取（C7.2/C7.3）；
- classifier DATA-only 冻结 schema 候选构建（语义变化→变更请求，C7.1）；
- validator  独立来源事实校验（禁同源互证；T2 须晋升 T1，C7.5a/C8.1）；
- testgate   隔离门禁（临时 store／无网络／无 session／不写指针，C8.4/C8.6/C8.7）；
- publisher  发布（复用 store 单事务原子＋publish 类 audit）与回滚（仅审计化
             指针，不法律时间旅行）；
- scheduler  调度时钟（格历月＋月末截断＋完成日重锚；singleflight；有界退避，
             C6）＋管线编排与 SOURCE 状态（C7.9）。

复用 app.rules.store.RuleStore 的既有原子发布/pin/audit 契约（不重写 store 层）。
下载/候选/LLM 内容永不可执行；更新程序不修改源码/测试/规格（C8.5）。
"""
