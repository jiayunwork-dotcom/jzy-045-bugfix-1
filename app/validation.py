"""入参校验。

本模块不感知 Flask 之外的业务：只负责把请求体里的值校验成
"有限的、满足符号约束的 float"，以及字符串 / 枚举 / 必填项检查。
校验失败统一抛 :class:`~app.errors.ValidationError`，消息带原因。
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ValidationError


def number(
    value: Any,
    field: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> float:
    """把入参校验为有限实数。

    :param positive: 要求严格大于 0。
    :param non_negative: 要求大于等于 0。
    """
    # bool 是 int 的子类，必须先排除：True/False 不是合法的动力学数值。
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(
            f"参数 {field!r} 必须是数值，收到的是 {type(value).__name__}。",
            field=field,
        )
    result = float(value)
    if not math.isfinite(result):
        raise ValidationError(
            f"参数 {field!r} 必须是有限数值，不能是 NaN 或无穷大。",
            field=field,
        )
    if positive and result <= 0.0:
        raise ValidationError(
            f"参数 {field!r} 必须为正数（> 0），收到的是 {result}。",
            field=field,
        )
    if non_negative and result < 0.0:
        raise ValidationError(
            f"参数 {field!r} 不能为负数，收到的是 {result}。",
            field=field,
        )
    return result


def require_positive(value: Any, field: str) -> float:
    """Vmax、Km、Ki 等必须严格为正的参数。"""
    return number(value, field, positive=True)


def require_non_negative(value: Any, field: str) -> float:
    """底物浓度 [S]、抑制剂浓度 [I] 允许为零、不允许为负。"""
    return number(value, field, non_negative=True)


def choice(value: Any, field: str, allowed: Sequence[str]) -> str:
    """枚举字段校验。"""
    if not isinstance(value, str):
        raise ValidationError(
            f"参数 {field!r} 必须是字符串，收到的是 {type(value).__name__}。",
            field=field,
        )
    if value not in allowed:
        allowed_repr = ", ".join(repr(item) for item in allowed)
        raise ValidationError(
            f"参数 {field!r} 的值 {value!r} 不被接受；允许的取值：{allowed_repr}。",
            field=field,
        )
    return value


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def name_string(value: Any, field: str) -> str:
    """酶参数档名称：1–64 个字符，字母/数字开头，仅含字母数字 _ . -。"""
    text = string(value, field, max_length=64)
    if not _NAME_RE.match(text):
        raise ValidationError(
            f"参数 {field!r} 必须以字母或数字开头，长度 1–64，"
            "且只能包含字母、数字、下划线、点或连字符。",
            field=field,
        )
    return text


def string(value: Any, field: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            f"参数 {field!r} 必须是字符串，收到的是 {type(value).__name__}。",
            field=field,
        )
    if len(value) == 0:
        raise ValidationError(f"参数 {field!r} 不能是空字符串。", field=field)
    if len(value) > max_length:
        raise ValidationError(
            f"参数 {field!r} 长度不能超过 {max_length} 个字符。", field=field
        )
    return value


def require_mapping(value: Any) -> Mapping[str, Any]:
    """请求体必须是 JSON 对象。"""
    if not isinstance(value, Mapping):
        raise ValidationError(
            "请求体必须是 JSON 对象（键值对形式）。",
        )
    return value


def reject_unknown_fields(
    raw: Mapping[str, Any], allowed: set[str], *, hint: str | None = None
) -> None:
    """严格字段白名单：多余字段直接报错，避免调用方把参数写进无效字段后被静默忽略。"""
    unknown = sorted(set(raw) - allowed)
    if unknown:
        detail = f"；{hint}" if hint else ""
        raise ValidationError(
            f"存在不被接受的字段：{', '.join(unknown)}。"
            f"允许的字段：{', '.join(sorted(allowed))}{detail}。",
            field=unknown[0],
        )


def require_fields(raw: Mapping[str, Any], required: Sequence[str]) -> None:
    missing = [field for field in required if field not in raw]
    if missing:
        raise ValidationError(
            f"缺少必填字段：{', '.join(missing)}。", field=missing[0]
        )
