"""竞争性抑制换算的单元测试。

重点钉住两条最容易在改写公式时被做坏的不变量：
1. 竞争性抑制下 Vmax_app 恒等于 Vmax（双倒数纵截距不变）；
2. 半饱和点随 [I] 增大向高底物浓度一侧移动（Km_app 增大）。
"""
from __future__ import annotations

import pytest

from app.errors import InhibitionError
from app.inhibition import (
    SUPPORTED_INHIBITION_TYPE,
    apparent_km,
    assert_competitive_only,
    assert_supported_type,
    competitive_factor,
    evaluate_competitive,
)
from app.kinetics import (
    lineweaver_burk_line,
    michaelis_rate,
)

VMAX, KM = 10.0, 0.1
KI = 0.05


def test_factor_is_one_plus_inhibitor_over_ki():
    assert competitive_factor(0.0, KI) == pytest.approx(1.0)
    assert competitive_factor(KI, KI) == pytest.approx(2.0)
    assert competitive_factor(2.0 * KI, KI) == pytest.approx(3.0)


def test_apparent_km_raised_and_vmax_unchanged():
    result = evaluate_competitive(VMAX, KM, 1.0, inhibitor=0.05, ki=KI)
    assert result.factor == pytest.approx(2.0)
    assert result.apparent_km == pytest.approx(0.2)
    assert result.apparent_vmax == VMAX  # 恒等，不是近似


def test_half_saturation_shifts_to_higher_substrate():
    # 半饱和点从 [S]=Km 移到 [S]=Km_app；在新半饱和点速率仍恰为 Vmax/2。
    result = evaluate_competitive(VMAX, KM, 0.2, inhibitor=0.05, ki=KI)
    assert result.rate == pytest.approx(5.0)
    # 原半饱和点在抑制下速率低于一半。
    at_old = evaluate_competitive(VMAX, KM, KM, inhibitor=0.05, ki=KI)
    assert at_old.rate < VMAX / 2


def test_increasing_inhibitor_only_moves_half_saturation_point():
    # 只增大 [I]：Km_app 单调增大，Vmax_app 始终不变。
    previous_km_app = KM
    for i in (0.0, 0.01, 0.05, 0.1, 0.5):
        r = evaluate_competitive(VMAX, KM, 1.0, inhibitor=i, ki=KI)
        assert r.apparent_km >= previous_km_app
        assert r.apparent_vmax == VMAX
        previous_km_app = r.apparent_km


def test_high_substrate_still_approaches_same_vmax_under_inhibition():
    # 极高底物浓度下，受抑制速率仍逼近同一个 Vmax。
    r = evaluate_competitive(VMAX, KM, 1.0e12, inhibitor=1.0, ki=KI)
    assert r.rate == pytest.approx(VMAX, rel=1e-9)
    assert r.apparent_vmax == VMAX


def test_lineweaver_burk_intercept_invariant():
    # 纵截距 1/Vmax 不随 [I] 变化；斜率随 [I] 增大而增大。
    slope0, intercept0 = lineweaver_burk_line(VMAX, VMAX)
    slopes = []
    intercepts = []
    for i in (0.0, 0.05, 0.1, 0.5):
        r = evaluate_competitive(VMAX, KM, 1.0, inhibitor=i, ki=KI)
        slope, intercept = lineweaver_burk_line(r.apparent_vmax, r.apparent_km)
        slopes.append(slope)
        intercepts.append(intercept)
    assert slopes == sorted(slopes)  # 斜率单调增大
    for intercept in intercepts:
        assert intercept == pytest.approx(intercept0)


def test_no_inhibitor_continuously_recovers_plain_michaelis():
    # [I]=0：表观 Km 即 Km，速率与无抑制逐项相同。
    for s in (0.0, 0.05, 0.1, 0.5, 10.0):
        inhibited = evaluate_competitive(VMAX, KM, s, inhibitor=0.0, ki=KI)
        plain = michaelis_rate(VMAX, KM, s)
        assert inhibited.rate == pytest.approx(plain)
        assert inhibited.apparent_km == pytest.approx(KM)


def test_huge_ki_suppresses_inhibition_continuously():
    # Ki 趋于极大，[I]/Ki 趋于零：连续退回无抑制，无跳变、无除零。
    for huge_ki in (1.0e12, 1.0e100, 1.0e300):
        factor = competitive_factor(1.0, huge_ki)
        assert factor == pytest.approx(1.0)
        r = evaluate_competitive(VMAX, KM, 0.3, inhibitor=1.0, ki=huge_ki)
        assert r.apparent_km == pytest.approx(KM)
        assert r.rate == pytest.approx(michaelis_rate(VMAX, KM, 0.3))


def test_vmax_doubles_rate_everywhere_even_under_inhibition():
    # 竞争性抑制不破坏 Vmax 的线性：Vmax 翻倍，任意点速率翻倍。
    for s in (0.0, 0.1, 0.2, 5.0):
        base = evaluate_competitive(10.0, KM, s, inhibitor=0.05, ki=KI)
        doubled = evaluate_competitive(20.0, KM, s, inhibitor=0.05, ki=KI)
        assert doubled.rate == pytest.approx(2.0 * base.rate)
        assert doubled.apparent_vmax == pytest.approx(2.0 * base.apparent_vmax)
        assert doubled.apparent_km == pytest.approx(base.apparent_km)


def test_hand_checkable_demo_numbers():
    # 己糖激酶示范锚点：[I]=Ki 时 factor=2、Km_app=0.2、Vmax_app=10。
    r = evaluate_competitive(10.0, 0.1, 0.2, inhibitor=0.05, ki=0.05)
    assert r.rate == pytest.approx(5.0)
    assert r.factor == 2.0
    assert r.apparent_km == 0.2
    assert r.apparent_vmax == 10.0


# ------------------------------------------------------------- 语义与矛盾参数


def test_only_competitive_type_accepted():
    assert_supported_type("competitive")
    for bad in ("noncompetitive", "non-competitive", "mixed", "uncompetitive", ""):
        with pytest.raises(InhibitionError):
            assert_supported_type(bad)


def test_noncompetitive_factor_fields_are_rejected_with_reason():
    for field in ("alpha", "beta"):
        with pytest.raises(InhibitionError) as excinfo:
            assert_competitive_only({field: 2.0})
        assert excinfo.value.field == field
        assert "竞争性" in excinfo.value.message


def test_vmax_factor_one_is_consistent_otherwise_rejected():
    assert_competitive_only({"vmax_factor": 1.0})  # 等于 1，与竞争性自洽
    with pytest.raises(InhibitionError):
        assert_competitive_only({"vmax_factor": 0.5})
    with pytest.raises(InhibitionError):
        assert_competitive_only({"vmax_factor": "1"})


def test_declared_factor_must_match_i_over_ki():
    evaluate_competitive(
        VMAX, KM, 0.2, inhibitor=0.05, ki=KI, declared_factor=2.0
    )
    with pytest.raises(InhibitionError) as excinfo:
        evaluate_competitive(
            VMAX, KM, 0.2, inhibitor=0.05, ki=KI, declared_factor=3.0
        )
    assert excinfo.value.field == "factor"


def test_apparent_km_helper():
    assert apparent_km(0.1, 2.0) == pytest.approx(0.2)
    assert apparent_km(0.1, 1.0) == pytest.approx(0.1)


def test_zero_substrate_under_inhibition_is_zero():
    r = evaluate_competitive(VMAX, KM, 0.0, inhibitor=0.5, ki=KI)
    assert r.rate == 0.0
    assert r.saturation_fraction == 0.0
    assert r.apparent_vmax == VMAX
