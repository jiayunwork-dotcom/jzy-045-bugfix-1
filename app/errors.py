"""跨模块共享的异常类型。

这些异常由 ``app.routes`` 中的错误处理器统一翻译成 HTTP JSON 响应。
"""
from __future__ import annotations


class AppError(Exception):
    """所有可向调用方展示的业务错误的基类。

    :param message: 带原因的错误说明（人类可读，中文）。
    :param status_code: 对应的 HTTP 状态码。
    :param code: 机器可读的错误代码。
    :param field: 与错误相关的请求字段名（如有）。
    """

    status_code: int = 400
    code: str = "invalid_request"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.field = field

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"error": self.code, "message": self.message}
        if self.field is not None:
            payload["field"] = self.field
        return payload


class ValidationError(AppError):
    """入参非法。"""

    status_code = 400
    code = "invalid_parameter"


class InhibitionError(AppError):
    """抑制类型与抑制因子参数语义矛盾。"""

    status_code = 422
    code = "inhibition_conflict"


class EnzymeNotFoundError(AppError):
    """按名取用酶参数档时找不到对应记录。"""

    status_code = 404
    code = "enzyme_not_found"


class EnzymeExistsError(AppError):
    """登记酶参数档时名称已存在。"""

    status_code = 409
    code = "enzyme_exists"


class StoreError(AppError):
    """参数档落地存储本身不可用（文件损坏、无法写入等）。"""

    status_code = 500
    code = "store_error"
