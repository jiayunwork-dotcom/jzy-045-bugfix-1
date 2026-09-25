"""竞争性抑制换算。

竞争性抑制的唯一定量作用是抬高表观米氏常数，最大速率保持不变::

    Km_app = Km * (1 + [I]/Ki)
    Vmax_app = Vmax
    v = Vmax * [S] / ([S] + Km_app)

本服务只支持竞争性（``inhibition_type="competitive"``）这一种语义。
非竞争性 / 混合型抑制下 Vmax 会被压低（典型形态 v = Vmax*[S] /
(beta*Km + alpha*[S])，``alpha``、``beta`` 是描述这类效应的因子）。
如果调用方声明竞争性却同时传入这些矛盾的抑制因子，
:func:`assert_competitive_only` 会以带原因的错误挡回，而不是猜测意图
静默改算。``vmax_factor`` 是同类信号，但它恰好为 1（竞争性下 Vmax
不变）时与竞争性自洽，因此单独做显式核对。
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isclose

from .errors import InhibitionError
from .kinetics import evaluate

SUPPORTED_INHIBITION_TYPE = "competitive"

# 非竞争性 / 混合型抑制的特征因子：竞争性语义下出现即矛盾。
# 注意 vmax_factor 不在此列：它恰好为 1 时与竞争性抑制兼容，
# 走下面的显式核对分支。
_NONCOMPETITIVE_FACTOR_FIELDS = ("alpha", "beta")

# 调用方自报抑制因子与 1+[I]/Ki 之间允许的相对偏差。
_FACTOR_REL_TOL = 1e-12


@dataclass(frozen=True, slots=True)
class InhibitionResult:
    """竞争性抑制条件下的完整计算结果。"""

    vmax: float
    km: float
    substrate: float
    inhibitor: float
    ki: float
    factor: float
    apparent_km: float
    apparent_vmax: float
    rate: float
    saturation_fraction: float

    def as_dict(self) -> dict[str, object]:
        return {
            "vmax": self.vmax,
            "km": self.km,
            "substrate": self.substrate,
            "inhibitor": self.inhibitor,
            "ki": self.ki,
            "inhibition_type": SUPPORTED_INHIBITION_TYPE,
            "factor": self.factor,
            "apparent_km": self.apparent_km,
            "apparent_vmax": self.apparent_vmax,
            "rate": self.rate,
            "saturation_fraction": self.saturation_fraction,
        }


def assert_supported_type(inhibition_type: str) -> None:
    """确认抑制类型语义。除竞争性外一律拒绝。"""
    if inhibition_type != SUPPORTED_INHIBITION_TYPE:
        raise InhibitionError(
            "不支持的抑制类型：本服务只接受竞争性抑制"
            f"（inhibition_type='{SUPPORTED_INHIBITION_TYPE}'）；"
            f"收到的是 {inhibition_type!r}，不会按非竞争性或混合型处理。",
            field="inhibition_type",
        )


def assert_competitive_only(raw: dict[str, object]) -> None:
    """挡回与竞争性抑制矛盾的抑制因子参数。

    对 ``vmax_factor`` 做语义核对（出现时）：竞争性抑制不改动 Vmax，
    只允许恰好为 1，否则视为矛盾参数。
    """
    for field in _NONCOMPETITIVE_FACTOR_FIELDS:
        if field in raw:
            raise InhibitionError(
                f"参数 {field!r} 用于描述非竞争性或混合型抑制（会改变 Vmax），"
                "与声明的竞争性抑制矛盾；本端点不会猜测调用意图，请改用"
                "竞争性参数（inhibitor、ki）描述或撤掉该因子。",
                field=field,
            )

    if "vmax_factor" in raw:
        value = raw["vmax_factor"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise InhibitionError(
                "vmax_factor 必须是数值；竞争性抑制下它只能等于 1。",
                field="vmax_factor",
            )
        if not isclose(float(value), 1.0, rel_tol=_FACTOR_REL_TOL, abs_tol=0.0):
            raise InhibitionError(
                f"vmax_factor={value} 意味着最大速率被改变，与竞争性抑制"
                "（Vmax_app 必须等于 Vmax）矛盾。",
                field="vmax_factor",
            )


def competitive_factor(inhibitor: float, ki: float) -> float:
    """计算抑制因子 alpha = 1 + [I]/Ki。

    调用约定：``inhibitor >= 0``、``ki > 0``（有限数），由上层保证。
    Ki 取极大时 [I]/Ki 在浮点上自然下溢为 0，结果连续退回 1，不存在
    除零或跳变问题。
    """
    return 1.0 + inhibitor / ki


def apparent_km(km: float, factor: float) -> float:
    """竞争性抑制下的表观米氏常数。"""
    return km * factor


def evaluate_competitive(
    vmax: float,
    km: float,
    substrate: float,
    inhibitor: float,
    ki: float,
    *,
    declared_factor: float | None = None,
) -> InhibitionResult:
    """计算竞争性抑制条件下的速率与表观常数。

    :param declared_factor: 调用方自报的抑制因子。给出时必须与
        ``1 + [I]/Ki`` 一致，否则以 :class:`InhibitionError` 挡回。
    """
    factor = competitive_factor(inhibitor, ki)

    if declared_factor is not None:
        if not isclose(
            declared_factor, factor, rel_tol=_FACTOR_REL_TOL, abs_tol=0.0
        ):
            raise InhibitionError(
                f"自报抑制因子 factor={declared_factor} 与由 [I]/Ki 计算出的"
                f" 1+[I]/Ki={factor} 不一致，参数互相矛盾，拒绝猜测。",
                field="factor",
            )
        factor = declared_factor

    km_app = apparent_km(km, factor)
    # 关键不变量：Vmax_app 恒等于 Vmax；速率只经由表观 Km 复用米氏核心，
    # 因而任何重构都不可能在这里压低最大速率。
    vmax_app = vmax
    result = evaluate(vmax_app, km_app, substrate)

    return InhibitionResult(
        vmax=vmax,
        km=km,
        substrate=substrate,
        inhibitor=inhibitor,
        ki=ki,
        factor=factor,
        apparent_km=km_app,
        apparent_vmax=vmax_app,
        rate=result.rate,
        saturation_fraction=result.saturation_fraction,
    )
