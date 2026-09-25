"""米氏核心的单元测试。"""
from __future__ import annotations

import math

import pytest

from app.kinetics import (
    evaluate,
    lineweaver_burk_line,
    lineweaver_burk_points,
    michaelis_rate,
    saturation_fraction,
)

VMAX, KM = 10.0, 0.1


def test_basic_michaelis_rate_hand_check():
    # [S] = Km 时速率恰为 Vmax 的一半（需求锚点）。
    assert michaelis_rate(VMAX, KM, KM) == pytest.approx(5.0)
    # 手算锚点：v = 10*0.3/(0.3+0.1) = 7.5
    assert michaelis_rate(VMAX, KM, 0.3) == pytest.approx(7.5)


def test_zero_substrate_is_legal_zero_not_error():
    # [S]=0 是合法结果：速率为零，饱和分数为零，不抛异常。
    result = evaluate(VMAX, KM, 0.0)
    assert result.rate == 0.0
    assert result.saturation_fraction == 0.0


def test_half_saturation_point():
    result = evaluate(VMAX, KM, KM)
    assert result.rate == pytest.approx(VMAX / 2)
    assert result.saturation_fraction == pytest.approx(0.5)


def test_rate_approaches_vmax_as_substrate_grows():
    # 极高底物浓度下速率逼近同一个 Vmax（单调、有上界）。
    previous = -1.0
    for exponent in range(-4, 8):
        s = KM * 10.0**exponent
        v = michaelis_rate(VMAX, KM, s)
        assert v > previous
        assert v <= VMAX
        previous = v
    assert michaelis_rate(VMAX, KM, 1.0e12) == pytest.approx(VMAX, rel=1e-9)


def test_saturation_fraction_range():
    assert saturation_fraction(0.0, KM) == 0.0
    assert saturation_fraction(KM, KM) == pytest.approx(0.5)
    assert saturation_fraction(1.0e9 * KM, KM) == pytest.approx(1.0, abs=1e-9)


def test_vmax_scales_rate_linearly():
    # 把 Vmax 整体放大两倍，任意底物浓度下速率同步放大两倍（回归护栏）。
    substrate_values = [0.0, 0.01, 0.1, 0.5, 10.0]
    for s in substrate_values:
        base = michaelis_rate(10.0, KM, s)
        doubled = michaelis_rate(20.0, KM, s)
        assert doubled == pytest.approx(2.0 * base)


def test_lineweaver_burk_line_coefficients():
    slope, intercept = lineweaver_burk_line(VMAX, KM)
    assert slope == pytest.approx(KM / VMAX)
    assert intercept == pytest.approx(1.0 / VMAX)


def test_lineweaver_burk_points_lie_on_the_line():
    points = lineweaver_burk_points(VMAX, KM, [0.02, 0.1, 0.5, 2.0])
    slope, intercept = lineweaver_burk_line(VMAX, KM)
    for inv_s, inv_v in points:
        assert inv_v == pytest.approx(slope * inv_s + intercept)


def test_kinetics_core_purely_numeric_no_validation_branches():
    # 核心模块不做校验：合法域内 NaN 不产生于常规输入。
    assert math.isfinite(michaelis_rate(VMAX, KM, 0.0))
