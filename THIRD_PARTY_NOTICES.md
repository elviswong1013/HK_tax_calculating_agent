# 第三方通知（THIRD PARTY NOTICES）

本清单汇总本项目使用的第三方组件、引用项目与官方资料的许可/版权信息。
各许可以其**包元数据与实际发布文本**为准；升级依赖后请重新核对。

## 运行时依赖

| 组件 | 许可 |
| --- | --- |
| [FastAPI](https://pypi.org/project/fastapi/) | MIT |
| [Jinja2](https://pypi.org/project/jinja2/) | BSD-3-Clause |
| [Pydantic](https://pypi.org/project/pydantic/) | MIT |
| [Uvicorn](https://pypi.org/project/uvicorn/) | BSD-3-Clause |
| [HTTPX](https://pypi.org/project/httpx/) | BSD-3-Clause |

## 开发/测试依赖

| 组件 | 许可 |
| --- | --- |
| [pytest](https://pypi.org/project/pytest/) | MIT |
| [Hypothesis](https://pypi.org/project/hypothesis/) | MPL-2.0 |
| [Playwright（Python 绑定）](https://pypi.org/project/playwright/) | Apache-2.0（浏览器内核另有其自身许可） |

## 参考项目（行为参考，未复制源码）

- [leeyc0/hksalariestax](https://github.com/leeyc0/hksalariestax)，固定 commit `94e4ddca53d792316e6a53b2fb2fa8cc9e571a91`，
  MIT License，Copyright (c) 2018 leeyc0。保留其版权与许可记录。

## 官方资料引用

- 项目文档（`docs/`）中对香港税务局（IRD）与 GovHK 材料的引用均为**短引用并注明出处**；
  官方来源版权归香港特别行政区政府所有（GovHK 版权条款：超出许可范围的复制/分发需事先书面同意）。
- 快照（如 `.playwright-mcp/`）仅本地保存用于核验，**不再分发**，并已列入 `.gitignore`。
- 本项目不隶属、不获认可于 IRD；见 [LEGAL.md](LEGAL.md)。
