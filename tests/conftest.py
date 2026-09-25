"""共享 pytest fixture。"""
from __future__ import annotations

import pytest

from app.enzyme_store import EnzymeStore
from app.routes import create_app


@pytest.fixture()
def store(tmp_path):
    return EnzymeStore(tmp_path / "enzymes.json")


@pytest.fixture()
def app(store):
    application = create_app(store=store)
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()
