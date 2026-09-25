"""米氏动力学核心计算。

本模块只做"纯数学"，不读取 HTTP 请求，也不抛出与参数相关的异常
（参数合法性由 :mod:`app.validation` 负责，路由层在调用本模块之前校验）。

无抑制时的米氏方程::

    v = Vmax * [S] / ([S] + Km)

Lineweaver–Burk 双倒数关系::

    1/v = (Km / Vmax) * (1/[S]) + 1/Vmax

    斜率 slope = Km / Vmax，纵截距 intercept = 1/Vmax

竞争性抑制只会把 Km 换成 Km_app，因此抑制下的速率/双倒数关系都可以
直接复用本模块，只需传入表观常数——这从结构上保证了"竞争性抑制不
改动 Vmax"这条不变量不可能在公式重构时被做坏。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KineticsResult:
    """一次米氏速率计算的完整结果。"""

    vmax: float
    km: float
    substrate: float
    rate: float
    saturation_fraction: float

    def as_dict(self) -> dict[str, float]:
        return {
            "vmax": self.vmax,
            "km": self.km,
            "substrate": self.substrate,
            "rate": self.rate,
            "saturation_fraction": self.saturation_fraction,
        }


def michaelis_rate(vmax: float, km: float, substrate: float) -> float:
    """按米氏方程计算反应速率。

    调用约定：``vmax > 0``、``km > 0``、``substrate >= 0``，由上层保证。
    ``substrate == 0`` 时代数上自然得到 0.0（合法结果，无需特判分支，
    也就不会在零浓度处引入跳变）。
    """
    return vmax * substrate / (substrate + km)


def saturation_fraction(substrate: float, km: float) -> float:
    """饱和分数 v / Vmax = [S] / ([S] + Km)。"""
    return substrate / (substrate + km)


def evaluate(vmax: float, km: float, substrate: float) -> KineticsResult:
    """计算速率并附带饱和分数。"""
    fraction = saturation_fraction(substrate, km)
    rate = vmax * fraction
    return KineticsResult(
        vmax=vmax,
        km=km,
        substrate=substrate,
        rate=rate,
        saturation_fraction=fraction,
    )


def lineweaver_burk_line(vmax: float, km: float) -> tuple[float, float]:
    """返回 Lineweaver–Burk 直线的 ``(斜率, 纵截距)``。

    即 ``1/v = slope * (1/[S]) + intercept``。
    """
    return km / vmax, 1.0 / vmax


def lineweaver_burk_points(
    vmax: float,
    km: float,
    substrate_points: list[float] | tuple[float, ...],
) -> list[tuple[float, float]]:
    """在给定的若干底物浓度上计算 ``(1/[S], 1/v)`` 点序列。

    底物浓度必须为正（双倒数在 [S]=0 处无定义）。供交叉校验使用：
    这些点必然落在 :func:`lineweaver_burk_line` 给出的直线上。
    """
    points: list[tuple[float, float]] = []
    for s in substrate_points:
        rate = michaelis_rate(vmax, km, s)
        points.append((1.0 / s, 1.0 / rate))
    return points
