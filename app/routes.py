"""HTTP 路由：把请求校验、米氏核心、竞争性抑制换算、参数档持久化装配起来。

端点一览（仅 JSON，无任何网页界面）：

* ``POST /rate``                无抑制速率 + 饱和分数
* ``POST /rate/inhibited``      竞争性抑制下的速率 + 表观 Km / 表观 Vmax
* ``GET  /health``              存活探针
* ``GET  /enzymes``             列出已登记酶参数档
* ``POST /enzymes``             登记新参数档（重名 -> 409）
* ``GET  /enzymes/<name>``      按名取用
* ``PUT  /enzymes/<name>``      覆盖登记
* ``DELETE /enzymes/<name>``    删除
"""
from __future__ import annotations

import os
from typing import Any

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from .enzyme_store import EnzymeStore
from .errors import AppError, InhibitionError, ValidationError
from .inhibition import (
    SUPPORTED_INHIBITION_TYPE,
    assert_competitive_only,
    evaluate_competitive,
)
from .kinetics import evaluate
from .validation import (
    name_string,
    reject_unknown_fields,
    require_fields,
    require_mapping,
    require_non_negative,
    require_positive,
    string,
)

bp = Blueprint("api", __name__)

RATE_FIELDS = {"enzyme", "vmax", "km", "substrate"}
INHIBITED_FIELDS = {
    "enzyme",
    "vmax",
    "km",
    "substrate",
    "inhibitor",
    "ki",
    "inhibition_type",
    # 可自报抑制因子，必须与 1+[I]/Ki 一致；vmax_factor 仅在等于 1 时
    # 与竞争性自洽，两者都由 inhibition 模块做语义核对。
    "factor",
    "vmax_factor",
}
ENZYME_FIELDS = {"name", "vmax", "km", "ki", "description"}


# ----------------------------------------------------------------- 小工具


def _store() -> EnzymeStore:
    return current_app.config["ENZYME_STORE"]


def json_object() -> dict[str, Any]:
    """解析请求体为 JSON 对象，空体 / 非法 JSON / 非对象一律 400。"""
    data = request.get_json(silent=True)
    if data is None:
        raise ValidationError(
            "请求体为空或不是合法 JSON；请以 application/json 提交一个 JSON 对象。"
        )
    return dict(require_mapping(data))


def _resolve_constants(raw: dict[str, Any]) -> dict[str, Any]:
    """解析 (Vmax, Km)：要么点名已登记酶，要么内联给出，二者互斥不可混用。"""
    has_enzyme = "enzyme" in raw
    if has_enzyme and not isinstance(raw["enzyme"], str):
        raise ValidationError(
            "参数 'enzyme' 必须是已登记酶参数档的名称字符串。", field="enzyme"
        )

    inline_fields = {"vmax", "km"} & raw.keys()
    profile: dict[str, Any] = {}

    if has_enzyme:
        if inline_fields:
            raise ValidationError(
                "'enzyme' 与内联常数 vmax/km 不能同时出现：请整体引用已登记酶，"
                "或整体内联给出 vmax、km，不要混用以免来源不清。",
                field="vmax" if "vmax" in raw else "km",
            )
        profile = _store().get(raw["enzyme"])  # 找不到 -> 404
    else:
        missing = [field for field in ("vmax", "km") if field not in raw]
        if missing:
            raise ValidationError(
                "缺少动力学常数：请通过 'enzyme' 引用已登记酶，或内联提供 "
                f"vmax 与 km（缺少：{', '.join(missing)}）。",
                field=missing[0],
            )

    vmax = require_positive(profile.get("vmax", raw.get("vmax")), "vmax")
    km = require_positive(profile.get("km", raw.get("km")), "km")
    return {
        "vmax": vmax,
        "km": km,
        "profile": profile,
        "source": f"enzyme:{raw['enzyme']}" if has_enzyme else "inline",
    }


def _build_enzyme_profile(raw: dict[str, Any]) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "name": name_string(raw["name"], "name"),
        "vmax": require_positive(raw["vmax"], "vmax"),
        "km": require_positive(raw["km"], "km"),
    }
    if "ki" in raw:
        profile["ki"] = require_positive(raw["ki"], "ki")
    if "description" in raw:
        profile["description"] = string(raw["description"], "description")
    return profile


# ----------------------------------------------------------------- 计算端点


@bp.post("/rate")
def rate_endpoint():
    """无抑制米氏速率。"""
    raw = json_object()
    reject_unknown_fields(
        raw,
        RATE_FIELDS,
        hint="竞争性抑制计算请使用 POST /rate/inhibited",
    )
    require_fields(raw, ["substrate"])
    consts = _resolve_constants(raw)
    substrate = require_non_negative(raw["substrate"], "substrate")

    result = evaluate(consts["vmax"], consts["km"], substrate).as_dict()
    result["constants_source"] = consts["source"]
    return jsonify(result), 200


@bp.post("/rate/inhibited")
def rate_inhibited_endpoint():
    """竞争性抑制条件下的速率与表观动力学常数。"""
    raw = json_object()
    # 先做抑制语义挡回（alpha/beta 等矛盾因子），再走字段白名单。
    assert_competitive_only(raw)
    reject_unknown_fields(raw, INHIBITED_FIELDS)
    require_fields(raw, ["substrate", "inhibitor", "inhibition_type"])

    inhibition_type = raw["inhibition_type"]
    if not isinstance(inhibition_type, str):
        raise ValidationError(
            "参数 'inhibition_type' 必须是字符串。", field="inhibition_type"
        )
    if inhibition_type != SUPPORTED_INHIBITION_TYPE:
        # 明确拒绝：不把竞争性端点悄悄当成非竞争性/混合型使用。
        raise InhibitionError(
            f"不支持的抑制类型 {inhibition_type!r}：本端点只提供竞争性抑制"
            f"（inhibition_type='{SUPPORTED_INHIBITION_TYPE}'），"
            "不会按非竞争性或混合型抑制处理。",
            field="inhibition_type",
        )

    consts = _resolve_constants(raw)
    substrate = require_non_negative(raw["substrate"], "substrate")
    inhibitor = require_non_negative(raw["inhibitor"], "inhibitor")

    profile = consts["profile"]
    # 抑制常数 Ki：优先采用参数档里登记的 Ki（酶自身的动力学属性），
    # 未登记时才回退到请求内联给出的 ki。
    if profile.get("ki") is not None:
        ki = require_positive(profile["ki"], "ki")
    elif "ki" in raw:
        ki = require_positive(raw["ki"], "ki")
    else:
        raise ValidationError(
            "竞争性抑制计算必须给出抑制常数 ki：请在请求内联提供，或使用预置了 "
            "ki 的酶参数档。",
            field="ki",
        )

    declared_factor = None
    if "factor" in raw:
        declared_factor = require_positive(raw["factor"], "factor")

    result = evaluate_competitive(
        consts["vmax"],
        consts["km"],
        substrate,
        inhibitor,
        ki,
        declared_factor=declared_factor,
    ).as_dict()
    result["constants_source"] = consts["source"]
    return jsonify(result), 200


# ------------------------------------------------------------- 酶参数档端点


@bp.get("/health")
def health_endpoint():
    return jsonify({"status": "ok", "service": "enzyme-kinetics"}), 200


@bp.get("/enzymes")
def list_enzymes_endpoint():
    return jsonify({"enzymes": _store().list_all()}), 200


@bp.get("/enzymes/<name>")
def get_enzyme_endpoint(name: str):
    return jsonify(_store().get(name)), 200


@bp.post("/enzymes")
def create_enzyme_endpoint():
    raw = json_object()
    reject_unknown_fields(raw, ENZYME_FIELDS)
    require_fields(raw, ["name", "vmax", "km"])
    profile = _build_enzyme_profile(raw)
    _store().create(profile)  # 重名 -> 409
    return jsonify(profile), 201


@bp.put("/enzymes/<name>")
def replace_enzyme_endpoint(name: str):
    raw = json_object()
    reject_unknown_fields(raw, ENZYME_FIELDS)
    require_fields(raw, ["vmax", "km"])
    if "name" in raw and raw["name"] != name:
        raise ValidationError(
            f"路径中的酶名 {name!r} 与请求体中的 name={raw['name']!r} 不一致。",
            field="name",
        )
    # PUT 为覆盖语义，但 ki / description 是可选项：未显式给出时沿用旧值，
    # 避免"只想改 Vmax 却把默认 Ki 抹掉"的意外。
    existing = _store().get(name) if _store().exists(name) else None
    for optional_field in ("ki", "description"):
        if optional_field not in raw and existing and optional_field in existing:
            raw[optional_field] = existing[optional_field]
    profile = _build_enzyme_profile({**raw, "name": name})
    created = _store().create(profile, overwrite=True)
    return jsonify(profile), (201 if created else 200)


@bp.delete("/enzymes/<name>")
def delete_enzyme_endpoint(name: str):
    _store().delete(name)  # 不存在 -> 404
    return jsonify({"deleted": name}), 200


# ----------------------------------------------------------------- 应用工厂


def create_app(
    store: EnzymeStore | None = None,
    *,
    store_path: str | None = None,
) -> Flask:
    """构造 Flask 应用。

    :param store: 可注入现成的酶参数档仓库（测试常用）。
    :param store_path: 未注入仓库时，指定参数档 JSON 路径。
    """
    app = Flask(__name__)
    app.config["ENZYME_STORE"] = store or EnzymeStore(
        store_path or os.environ.get("ENZYME_STORE_PATH", "/data/enzymes.json")
    )

    @app.errorhandler(AppError)
    def handle_app_error(exc: AppError):
        return jsonify(exc.to_payload()), exc.status_code

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        return (
            jsonify({"error": exc.name.lower().replace(" ", "_"), "message": exc.description}),
            exc.code,
        )

    app.register_blueprint(bp)
    return app


app = create_app()
