"""统一错误类型与错误→HTTP 映射（Annex C C3.7 权威全表；SDD §4 错误响应统一形状）。

统一形状：{"error": {"code", "message_zh", "field", "details", "binding"}}；
错误码权威全表＝Annex C C3.7——新增错误码必须先入该表，再入此处实现。
"""

from __future__ import annotations

from typing import Any

# C3.7 错误码 → HTTP 状态（权威全表实现）
HTTP_STATUS_BY_CODE: dict[str, int] = {
    "E_INPUT_MISSING": 422,
    "E_INPUT_TYPE": 422,
    "E_INPUT_EXTRA": 422,
    "E_INPUT_OVERSIZE": 413,
    "E_INPUT_CONTRADICTORY": 422,
    "E_DEDUCTION_UNSUPPORTED": 422,
    "E_ELIGIBILITY_UNKNOWN": 422,
    "E_PERIOD_NOT_SUPPORTED": 422,
    "E_RULES_STATE_UNVERIFIABLE": 422,
    "E_TIME_POINT_REQUIRED": 422,
    "E_BUNDLE_INCOMPATIBLE": 409,
    "E_CONFIRMATION_REQUIRED": 403,
    "E_CONFIRMATION_STALE": 409,
    "E_CONFIRMATION_CONSUMED": 409,
    "E_CONFIRMATION_TARGET_MISMATCH": 409,
    "E_CONSENT_REQUIRED": 403,
    "E_CONSENT_STALE": 409,
    "E_CSRF": 403,
    "E_ORIGIN": 403,
    "E_HOST": 403,
    "E_MODEL_UNCONFIGURED": 409,
    "E_MODEL_AUTH": 502,
    "E_MODEL_RATE_LIMIT": 502,
    "E_MODEL_TIMEOUT": 502,
    "E_MODEL_BAD_RESPONSE": 502,
    "E_UPDATE_IN_PROGRESS": 202,
    "E_UPDATE_QUARANTINED": 409,
    "E_UPDATE_ROLLBACK_INVALID": 409,
}


class AppError(Exception):
    """业务/校验错误：携带 C3.7 错误码与统一形状负载。

    extra 中的键合并进响应顶层（如 E_ELIGIBILITY_UNKNOWN 的 questions[]）。
    """

    def __init__(
        self,
        code: str,
        message_zh: str,
        *,
        field: str | None = None,
        details: list[Any] | None = None,
        binding: dict[str, Any] | None = None,
        http_status: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if code not in HTTP_STATUS_BY_CODE:
            raise ValueError(f"未知错误码（须先入 Annex C C3.7 全表）：{code!r}")
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.field = field
        self.details = list(details or [])
        self.binding = binding
        self.http_status = http_status or HTTP_STATUS_BY_CODE[code]
        self.extra = dict(extra or {})

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message_zh": self.message_zh,
                "field": self.field,
                "details": self.details,
                "binding": self.binding,
            }
        }
        payload.update(self.extra)
        return payload
