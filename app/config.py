"""运行配置（Annex C C1/C5：默认回环 8000；DB 默认 %LOCALAPPDATA%，HKTAX_DB_PATH 可覆盖）。"""

from __future__ import annotations

import os
from pathlib import Path

ENGINE_VERSION = "0.1.0"
RULES_SCHEMA_VERSION = "1.0.0"
DEFAULT_PORT = 8000
PREPARE_TTL_SECONDS = 300  # C3.2.1：prepared TTL
CONFIRMATION_TTL_SECONDS = 300  # C3.2.2：确认能力 TTL
MAX_BODY_BYTES = 1_048_576  # C2.9：请求体 ≤1MB
SUPPORTED_YEARS = ("2024_25", "2025_26", "2026_27")  # SDD §1 直接税年度
SESSION_COOKIE_NAME = "hktax_session"  # C5.3：不透明 HttpOnly 会话 cookie


def db_path_from_env() -> Path:
    """SDD §8：运行时默认 %LOCALAPPDATA%\\hktax-agent\\rules.db；HKTAX_DB_PATH 可覆盖。"""
    override = os.environ.get("HKTAX_DB_PATH")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "hktax-agent" / "rules.db"
