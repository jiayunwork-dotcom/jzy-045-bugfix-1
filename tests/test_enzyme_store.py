"""酶参数档仓库：落地持久化、预置示范酶、并发隔离。"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.enzyme_store import EnzymeStore
from app.errors import EnzymeExistsError, EnzymeNotFoundError, StoreError


@pytest.fixture()
def store(tmp_path):
    return EnzymeStore(tmp_path / "data" / "enzymes.json")


def test_hexokinase_seeded_and_hand_checkable(store):
    hk = store.get("hexokinase")
    assert hk["vmax"] == 10.0
    assert hk["km"] == 0.1
    assert hk["ki"] == 0.05  # 预置 Ki，抑制请求可只点名酶并给 [I]


def test_persistence_survives_reinstantiation(store, tmp_path):
    path = tmp_path / "data" / "enzymes.json"
    store.create({"name": "pfk", "vmax": 4.0, "km": 0.2, "ki": 0.3})

    # 用同一个文件重新打开仓库（模拟服务重启），记录仍在。
    reopened = EnzymeStore(path)
    assert reopened.get("pfk")["km"] == 0.2
    assert reopened.get("hexokinase")["vmax"] == 10.0


def test_file_on_disk_is_json(store, tmp_path):
    store.create({"name": "ldh", "vmax": 7.0, "km": 0.8})
    data = json.loads((tmp_path / "data" / "enzymes.json").read_text("utf-8"))
    assert set(data) >= {"hexokinase", "ldh"}


def test_get_missing_raises_404(store):
    with pytest.raises(EnzymeNotFoundError):
        store.get("ghost")


def test_create_duplicate_conflicts(store):
    profile = {"name": "dup", "vmax": 1.0, "km": 1.0}
    assert store.create(profile) is True
    with pytest.raises(EnzymeExistsError):
        store.create(profile)
    # PUT 语义：overwrite=True 返回 False
    assert store.create({**profile, "vmax": 2.0}, overwrite=True) is False
    assert store.get("dup")["vmax"] == 2.0


def test_delete(store):
    store.create({"name": "tmp_enzyme", "vmax": 1.0, "km": 1.0})
    store.delete("tmp_enzyme")
    with pytest.raises(EnzymeNotFoundError):
        store.get("tmp_enzyme")
    with pytest.raises(EnzymeNotFoundError):
        store.delete("tmp_enzyme")


def test_corrupt_store_file_raises_store_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(StoreError):
        EnzymeStore(path)


def test_concurrent_creates_of_distinct_names_do_not_overwrite(store, tmp_path):
    def make(i: int):
        store.create({"name": f"enzyme_{i:03d}", "vmax": float(i), "km": 1.0})
        # 每次登记后立刻验证自己之前的记录仍完好（不被并发登记覆盖）。
        return store.get(f"enzyme_{i:03d}")["vmax"]

    with ThreadPoolExecutor(max_workers=16) as pool:
        values = list(pool.map(make, range(80)))

    assert values == [float(i) for i in range(80)]
    reopened = EnzymeStore(tmp_path / "data" / "enzymes.json")
    assert len(reopened.list_all()) == 81  # 80 个新酶 + hexokinase
    assert reopened.get("enzyme_000")["vmax"] == 0.0
    assert reopened.get("enzyme_079")["vmax"] == 79.0


def test_concurrent_creates_of_same_name_only_one_wins(store):
    profile = {"name": "race", "vmax": 1.0, "km": 1.0}
    outcomes: list[str] = []
    barrier = threading.Barrier(20)

    def attempt():
        barrier.wait()
        try:
            store.create(profile)
            outcomes.append("created")
        except EnzymeExistsError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("created") == 1
    assert outcomes.count("conflict") == 19
    assert store.get("race") is not None
