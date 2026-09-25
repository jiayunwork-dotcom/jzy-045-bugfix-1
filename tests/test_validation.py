"""入参校验模块的单元测试。"""
from __future__ import annotations

import math

import pytest

from app.errors import ValidationError
from app.validation import (
    choice,
    name_string,
    reject_unknown_fields,
    require_fields,
    require_mapping,
    require_non_negative,
    require_positive,
    string,
)


def test_positive_accepts_int_and_float():
    assert require_positive(3, "vmax") == 3.0
    assert require_positive(0.25, "km") == 0.25


@pytest.mark.parametrize("bad", [0, -1, -0.5])
def test_positive_rejects_non_positive(bad):
    with pytest.raises(ValidationError) as excinfo:
        require_positive(bad, "vmax")
    assert excinfo.value.field == "vmax"
    assert "正数" in excinfo.value.message


def test_non_negative_accepts_zero_rejects_negative():
    assert require_non_negative(0, "substrate") == 0.0
    with pytest.raises(ValidationError) as excinfo:
        require_non_negative(-0.01, "substrate")
    assert "负数" in excinfo.value.message


def test_bool_is_not_a_number():
    # bool 是 int 子类，但 True/False 不应被当成 1/0 接受。
    with pytest.raises(ValidationError):
        require_positive(True, "vmax")
    with pytest.raises(ValidationError):
        require_non_negative(False, "substrate")


def test_non_numeric_types_rejected():
    for bad in ("10", None, [1.0], {"x": 1}):
        with pytest.raises(ValidationError):
            require_positive(bad, "km")


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_rejected(bad):
    with pytest.raises(ValidationError):
        require_positive(bad, "ki")


def test_choice():
    assert choice("competitive", "inhibition_type", ["competitive"]) == "competitive"
    with pytest.raises(ValidationError):
        choice("mixed", "inhibition_type", ["competitive"])
    with pytest.raises(ValidationError):
        choice(1, "inhibition_type", ["competitive"])


def test_name_string_rules():
    assert name_string("hexokinase", "name") == "hexokinase"
    assert name_string("Myo-kinase_2.v1", "name")
    for bad in ("", "-badstart", "x" * 65, "bad name", "bad/name"):
        with pytest.raises(ValidationError):
            name_string(bad, "name")


def test_string_nonempty_and_length():
    assert string("ok", "description") == "ok"
    with pytest.raises(ValidationError):
        string("", "description")
    with pytest.raises(ValidationError):
        string("x" * 300, "description", max_length=10)


def test_reject_unknown_fields():
    reject_unknown_fields({"a": 1}, {"a", "b"})
    with pytest.raises(ValidationError) as excinfo:
        reject_unknown_fields({"a": 1, "zzz": 2}, {"a"})
    assert "zzz" in excinfo.value.message


def test_require_fields_reports_missing():
    require_fields({"a": 1, "b": 2}, ["a"])
    with pytest.raises(ValidationError) as excinfo:
        require_fields({"a": 1}, ["a", "b"])
    assert "b" in excinfo.value.message


def test_require_mapping():
    assert require_mapping({"a": 1}) == {"a": 1}
    for bad in ([], "x", 1, None):
        with pytest.raises(ValidationError):
            require_mapping(bad)
