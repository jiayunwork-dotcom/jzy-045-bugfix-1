"""端到端 HTTP 接口测试（Flask test client）。

覆盖：
* 两类计算端点的正常值与需求不变量；
* 非法输入逐条返回带原因的错误；
* 竞争性矛盾参数挡回、抑制类型只接受 competitive；
* 具名酶登记 / 覆盖 / 取用 / 删除 / 重启持久化（通过新仓库实例模拟）；
* 并发请求下计算独立、登记不互相覆盖（见 test_concurrency_http）。
"""
from __future__ import annotations

import math

import pytest

from app.enzyme_store import EnzymeStore
from app.routes import create_app


# ---------------------------------------------------------------- 无抑制速率


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_rate_inline_basic(client):
    resp = client.post("/rate", json={"vmax": 10.0, "km": 0.1, "substrate": 0.3})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["rate"] == pytest.approx(7.5)
    assert body["saturation_fraction"] == pytest.approx(0.75)
    assert body["constants_source"] == "inline"


def test_rate_half_saturation_invariant(client):
    resp = client.post("/rate", json={"vmax": 8.0, "km": 2.0, "substrate": 2.0})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["rate"] == pytest.approx(4.0)
    assert body["saturation_fraction"] == pytest.approx(0.5)


def test_rate_zero_substrate_is_legal_zero(client):
    resp = client.post("/rate", json={"vmax": 10.0, "km": 0.1, "substrate": 0})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["rate"] == 0.0
    assert body["saturation_fraction"] == 0.0


@pytest.mark.parametrize(
    "payload",
    [
        {"vmax": 0, "km": 0.1, "substrate": 0.1},
        {"vmax": -5, "km": 0.1, "substrate": 0.1},
        {"vmax": 10, "km": 0, "substrate": 0.1},
        {"vmax": 10, "km": -0.2, "substrate": 0.1},
        {"vmax": 10, "km": 0.1, "substrate": -0.01},
    ],
)
def test_rate_invalid_inputs_rejected_with_reason(client, payload):
    resp = client.post("/rate", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "invalid_parameter"
    assert body["field"] in ("vmax", "km", "substrate")
    assert body["message"]


def test_rate_non_finite_and_bad_types(client):
    for bad in (math.nan, math.inf):
        resp = client.post("/rate", json={"vmax": bad, "km": 0.1, "substrate": 0.1})
        assert resp.status_code == 400
    resp = client.post(
        "/rate", json={"vmax": "fast", "km": 0.1, "substrate": 0.1}
    )
    assert resp.status_code == 400
    # bool 不能蒙混成数值
    resp = client.post("/rate", json={"vmax": True, "km": 0.1, "substrate": 0.1})
    assert resp.status_code == 400


def test_rate_vmax_doubling_scales_rate(client):
    payload = {"km": 0.1, "substrate": 0.25}
    base = client.post("/rate", json={"vmax": 10, **payload}).get_json()
    doubled = client.post("/rate", json={"vmax": 20, **payload}).get_json()
    assert doubled["rate"] == pytest.approx(2 * base["rate"])


def test_rate_unknown_field_rejected(client):
    resp = client.post(
        "/rate",
        json={"vmax": 10, "km": 0.1, "substrate": 0.1, "inhibitor": 0.01},
    )
    assert resp.status_code == 400
    assert "inhibitor" in resp.get_json()["message"]


def test_rate_requires_constants(client):
    resp = client.post("/rate", json={"substrate": 0.1})
    assert resp.status_code == 400
    assert "vmax" in resp.get_json()["field"]


def test_rate_enzyme_and_inline_mutually_exclusive(client):
    resp = client.post(
        "/rate",
        json={"enzyme": "hexokinase", "vmax": 10, "km": 0.1, "substrate": 0.1},
    )
    assert resp.status_code == 400
    assert "混用" in resp.get_json()["message"]


def test_rate_missing_enzyme_404(client):
    resp = client.post("/rate", json={"enzyme": "nope", "substrate": 0.1})
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "enzyme_not_found"


def test_body_must_be_json_object(client):
    assert client.post("/rate", data="", content_type="application/json").status_code == 400
    assert client.post("/rate", json=[1, 2, 3]).status_code == 400
    assert client.post("/rate", json="hello").status_code == 400


# ------------------------------------------------------------- 竞争性抑制端点


def test_inhibited_hexokinase_hand_anchors(client):
    resp = client.post(
        "/rate/inhibited",
        json={
            "enzyme": "hexokinase",
            "substrate": 0.2,
            "inhibitor": 0.05,
            "inhibition_type": "competitive",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["factor"] == pytest.approx(2.0)
    assert body["apparent_km"] == pytest.approx(0.2)
    assert body["apparent_vmax"] == 10.0  # 最大速率不变
    assert body["rate"] == pytest.approx(5.0)  # 在 Km_app 处半饱和


def test_inhibited_vmax_invariant_across_inhibitor_levels(client):
    # 只增大 [I]：Vmax_app 恒定，Km_app 单调增大，高底物下仍逼近 Vmax。
    km_apps = []
    for i in (0.0, 0.01, 0.05, 0.2):
        body = client.post(
            "/rate/inhibited",
            json={
                "vmax": 10.0,
                "km": 0.1,
                "substrate": 0.1,
                "inhibitor": i,
                "ki": 0.05,
                "inhibition_type": "competitive",
            },
        ).get_json()
        assert body["apparent_vmax"] == 10.0
        km_apps.append(body["apparent_km"])
    assert km_apps == sorted(km_apps)
    assert km_apps[0] == pytest.approx(0.1)

    saturated = client.post(
        "/rate/inhibited",
        json={
            "vmax": 10.0,
            "km": 0.1,
            "substrate": 1e9,
            "inhibitor": 1.0,
            "ki": 0.05,
            "inhibition_type": "competitive",
        },
    ).get_json()
    assert saturated["rate"] == pytest.approx(10.0, rel=1e-6)


def test_inhibited_zero_inhibitor_and_huge_ki_recover_plain(client):
    base = client.post(
        "/rate", json={"vmax": 10.0, "km": 0.1, "substrate": 0.3}
    ).get_json()
    no_i = client.post(
        "/rate/inhibited",
        json={
            "vmax": 10.0,
            "km": 0.1,
            "substrate": 0.3,
            "inhibitor": 0,
            "ki": 0.05,
            "inhibition_type": "competitive",
        },
    ).get_json()
    huge_ki = client.post(
        "/rate/inhibited",
        json={
            "vmax": 10.0,
            "km": 0.1,
            "substrate": 0.3,
            "inhibitor": 1.0,
            "ki": 1e200,
            "inhibition_type": "competitive",
        },
    ).get_json()
    assert no_i["rate"] == pytest.approx(base["rate"])
    assert huge_ki["rate"] == pytest.approx(base["rate"])
    assert huge_ki["factor"] == pytest.approx(1.0)


def test_inhibited_requires_ki(client):
    resp = client.post(
        "/rate/inhibited",
        json={
            "vmax": 10.0,
            "km": 0.1,
            "substrate": 0.1,
            "inhibitor": 0.05,
            "inhibition_type": "competitive",
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "ki"


def test_inhibited_request_ki_overrides_enzyme_default(client):
    # 回归：点名已登记酶（默认 Ki=0.05）的同时，本次请求另给 ki=0.2，
    # 必须以请求值为准：factor = 1 + 0.05/0.2 = 1.25，而不是按默认值的 2.0。
    client.post(
        "/enzymes",
        json={"name": "ki_defaulted", "vmax": 10.0, "km": 0.1, "ki": 0.05},
    )
    resp = client.post(
        "/rate/inhibited",
        json={
            "enzyme": "ki_defaulted",
            "substrate": 0.2,
            "inhibitor": 0.05,
            "ki": 0.2,
            "inhibition_type": "competitive",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ki"] == pytest.approx(0.2)
    assert body["factor"] == pytest.approx(1.25)
    assert body["apparent_km"] == pytest.approx(0.125)
    assert body["apparent_vmax"] == 10.0  # 最大速率不变
    assert body["rate"] == pytest.approx(10.0 * 0.2 / (0.2 + 0.125))

    # 对照：同一次计算不另给 ki 时，仍回退到登记的默认 Ki=0.05。
    fallback = client.post(
        "/rate/inhibited",
        json={
            "enzyme": "ki_defaulted",
            "substrate": 0.2,
            "inhibitor": 0.05,
            "inhibition_type": "competitive",
        },
    ).get_json()
    assert fallback["ki"] == pytest.approx(0.05)
    assert fallback["factor"] == pytest.approx(2.0)
    assert fallback["apparent_km"] == pytest.approx(0.2)


def test_inhibited_rejects_noncompetitive_type(client):
    resp = client.post(
        "/rate/inhibited",
        json={
            "vmax": 10,
            "km": 0.1,
            "substrate": 0.1,
            "inhibitor": 0.05,
            "ki": 0.05,
            "inhibition_type": "noncompetitive",
        },
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "inhibition_conflict"
    assert "竞争性" in resp.get_json()["message"]


def test_inhibited_rejects_non_string_type(client):
    resp = client.post(
        "/rate/inhibited",
        json={
            "vmax": 10,
            "km": 0.1,
            "substrate": 0.1,
            "inhibitor": 0.05,
            "ki": 0.05,
            "inhibition_type": 1,
        },
    )
    assert resp.status_code == 400


def test_inhibited_rejects_contradictory_factors(client):
    common = {
        "vmax": 10,
        "km": 0.1,
        "substrate": 0.1,
        "inhibitor": 0.05,
        "ki": 0.05,
        "inhibition_type": "competitive",
    }
    for field, value in (("alpha", 2.0), ("beta", 0.5), ("vmax_factor", 0.5)):
        resp = client.post("/rate/inhibited", json={**common, field: value})
        assert resp.status_code == 422, field
        assert resp.get_json()["field"] == field


def test_inhibited_accepts_consistent_factor_pair(client):
    common = {
        "vmax": 10,
        "km": 0.1,
        "substrate": 0.2,
        "inhibitor": 0.05,
        "ki": 0.05,
        "inhibition_type": "competitive",
    }
    # 自报 factor 与 1+[I]/Ki 一致 -> 接受；vmax_factor=1 与竞争性自洽 -> 接受
    resp = client.post(
        "/rate/inhibited", json={**common, "factor": 2.0, "vmax_factor": 1.0}
    )
    assert resp.status_code == 200
    # 自报 factor 矛盾 -> 挡回
    resp = client.post("/rate/inhibited", json={**common, "factor": 3.0})
    assert resp.status_code == 422


def test_inhibited_invalid_numbers(client):
    common = {
        "vmax": 10,
        "km": 0.1,
        "substrate": 0.1,
        "inhibitor": 0.05,
        "ki": 0.05,
        "inhibition_type": "competitive",
    }
    assert client.post(
        "/rate/inhibited", json={**common, "ki": 0}
    ).status_code == 400
    assert client.post(
        "/rate/inhibited", json={**common, "inhibitor": -1}
    ).status_code == 400
    assert client.post(
        "/rate/inhibited", json={**common, "substrate": -1}
    ).status_code == 400
    # 缺少必填的 inhibition_type / inhibitor
    partial = {k: v for k, v in common.items() if k != "inhibition_type"}
    assert client.post("/rate/inhibited", json=partial).status_code == 400


def test_inhibited_zero_substrate_is_zero(client):
    resp = client.post(
        "/rate/inhibited",
        json={
            "vmax": 10,
            "km": 0.1,
            "substrate": 0,
            "inhibitor": 0.2,
            "ki": 0.05,
            "inhibition_type": "competitive",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["rate"] == 0.0
    assert body["apparent_vmax"] == 10.0


# ------------------------------------------------------------- 酶参数档管理


def test_enzyme_crud_cycle(client):
    created = client.post(
        "/enzymes",
        json={"name": "pfk", "vmax": 4.0, "km": 0.2, "ki": 0.3, "description": "PFK"},
    )
    assert created.status_code == 201

    duplicate = client.post("/enzymes", json={"name": "pfk", "vmax": 1.0, "km": 1.0})
    assert duplicate.status_code == 409

    fetched = client.get("/enzymes/pfk")
    assert fetched.status_code == 200
    assert fetched.get_json()["description"] == "PFK"

    listed = client.get("/enzymes").get_json()["enzymes"]
    assert {e["name"] for e in listed} == {"hexokinase", "pfk"}

    put = client.put("/enzymes/pfk", json={"vmax": 9.0, "km": 0.5})
    assert put.status_code == 200
    assert client.get("/enzymes/pfk").get_json()["vmax"] == 9.0
    assert client.get("/enzymes/pfk").get_json()["ki"] == 0.3  # 未覆盖的字段保留

    put_new = client.put("/enzymes/ldh", json={"vmax": 1.0, "km": 1.0})
    assert put_new.status_code == 201

    deleted = client.delete("/enzymes/pfk")
    assert deleted.status_code == 200
    assert client.get("/enzymes/pfk").status_code == 404
    assert client.delete("/enzymes/pfk").status_code == 404


def test_enzyme_validation(client):
    resp = client.post("/enzymes", json={"name": "bad name", "vmax": 1, "km": 1})
    assert resp.status_code == 400
    resp = client.post("/enzymes", json={"name": "x", "vmax": 0, "km": 1})
    assert resp.status_code == 400
    resp = client.post("/enzymes", json={"name": "x", "vmax": 1, "km": -1})
    assert resp.status_code == 400
    resp = client.post(
        "/enzymes", json={"name": "x", "vmax": 1, "km": 1, "ki": -2}
    )
    assert resp.status_code == 400
    resp = client.put("/enzymes/a", json={"name": "b", "vmax": 1, "km": 1})
    assert resp.status_code == 400


def test_registered_enzyme_usable_for_both_endpoints(client):
    client.post(
        "/enzymes",
        json={"name": "custom", "vmax": 12.0, "km": 0.4, "ki": 0.8},
    )
    rate = client.post("/rate", json={"enzyme": "custom", "substrate": 0.4})
    assert rate.get_json()["rate"] == pytest.approx(6.0)
    assert rate.get_json()["constants_source"] == "enzyme:custom"

    inhibited = client.post(
        "/rate/inhibited",
        json={
            "enzyme": "custom",
            "substrate": 0.8,
            "inhibitor": 0.8,
            "inhibition_type": "competitive",
        },
    )
    body = inhibited.get_json()
    assert body["factor"] == pytest.approx(2.0)
    assert body["apparent_km"] == pytest.approx(0.8)
    assert body["apparent_vmax"] == 12.0
    assert body["rate"] == pytest.approx(6.0)


def test_persistence_across_restart(tmp_path):
    path = tmp_path / "enz.json"
    app1 = create_app(store=EnzymeStore(path))
    client1 = app1.test_client()
    client1.post("/enzymes", json={"name": "survivor", "vmax": 3.0, "km": 0.6})

    # 模拟进程重启：以同一文件新建仓库与应用
    app2 = create_app(store=EnzymeStore(path))
    client2 = app2.test_client()
    body = client2.post("/rate", json={"enzyme": "survivor", "substrate": 0.6})
    assert body.status_code == 200
    assert body.get_json()["rate"] == pytest.approx(1.5)
    # 预置示范酶也还在
    assert client2.get("/enzymes/hexokinase").status_code == 200


def test_unknown_route_404_json(client):
    resp = client.get("/nope")
    assert resp.status_code == 404
    assert resp.is_json
