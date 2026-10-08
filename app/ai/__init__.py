"""AI 模型集成层（M7；REQ-11/14；Annex C C3.6/C4）。

- adapters：生产默认出站（HTTPX content=frozen_bytes；经 app.state.model_outbound
  接缝装配，C4.6 ⑦段）。
- tools：C3.6 封闭工具注册表与响应双层契约（wire 形状校验 → 内部规范消息 →
  strict 工具执行）。
"""
