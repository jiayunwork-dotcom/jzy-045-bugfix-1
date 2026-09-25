"""真实 HTTP 层并发护栏。

用 Werkzeug 的线程化服务器监听真实 socket，从多线程并发打请求，
验证：各自计算独立、酶登记互不覆盖、结果不串。
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib import error, request

import pytest
from werkzeug.serving import make_server

from app.enzyme_store import EnzymeStore
from app.routes import create_app


class LiveServer:
    def __init__(self, base_url: str):
        self.base_url = base_url


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    path = tmp_path_factory.mktemp("live") / "enzymes.json"
    store = EnzymeStore(path)
    app = create_app(store=store)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LiveServer(f"http://127.0.0.1:{port}")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _post(base_url: str, path: str, payload: dict) -> tuple[int, dict]:
    req = request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_concurrent_rate_requests_are_independent(live_server):
    # 不同 Vmax/底物的请求混在一起并发，每个结果必须只对应自己的入参。
    cases = [
        (10.0, 0.1, s, 10.0 * s / (s + 0.1))
        for s in (0.0, 0.05, 0.1, 0.3, 1.0, 5.0)
    ] * 40

    def one(case):
        vmax, km, s, expected = case
        status, body = _post(
            live_server.base_url,
            "/rate",
            {"vmax": vmax, "km": km, "substrate": s},
        )
        assert status == 200
        assert body["rate"] == pytest.approx(expected)
        assert body["substrate"] == s  # 响应不串号
        return body["rate"]

    with ThreadPoolExecutor(max_workers=24) as pool:
        results = list(pool.map(one, cases))
    assert len(results) == len(cases)


def test_concurrent_calculations_and_registrations_do_not_cross(live_server):
    # 一边持续登记新酶、一边用示范酶做抑制计算，两类结果互不污染。
    stop = threading.Event()
    errors: list[Exception] = []

    def calculate():
        while not stop.is_set():
            try:
                status, body = _post(
                    live_server.base_url,
                    "/rate/inhibited",
                    {
                        "enzyme": "hexokinase",
                        "substrate": 0.2,
                        "inhibitor": 0.05,
                        "inhibition_type": "competitive",
                    },
                )
                assert status == 200
                assert body["apparent_km"] == pytest.approx(0.2)
                assert body["apparent_vmax"] == 10.0
                assert body["rate"] == pytest.approx(5.0)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    def register(i: int):
        status, _ = _post(
            live_server.base_url,
            "/enzymes",
            {"name": f"parallel_{i:03d}", "vmax": float(i + 1), "km": 0.2},
        )
        return status

    calc_threads = [threading.Thread(target=calculate) for _ in range(6)]
    for t in calc_threads:
        t.start()
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(register, range(60)))
    stop.set()
    for t in calc_threads:
        t.join(timeout=10)

    assert not errors
    assert statuses.count(201) == 60
    status, body = _post(
        live_server.base_url, "/rate", {"enzyme": "parallel_042", "substrate": 0.2}
    )
    assert status == 200
    assert body["rate"] == pytest.approx(21.5)  # Vmax=43, Km=0.2 -> 43*0.2/0.4


def test_concurrent_same_name_registration_single_winner(live_server):
    barrier = threading.Barrier(16)

    def attempt(_):
        barrier.wait()
        return _post(
            live_server.base_url,
            "/enzymes",
            {"name": "same_name_race", "vmax": 1.0, "km": 1.0},
        )[0]

    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(attempt, range(16)))
    assert statuses.count(201) == 1
    assert statuses.count(409) == 15
