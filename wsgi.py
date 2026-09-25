"""WSGI 入口。

容器内由 waitress 以 ``--call wsgi:app`` 方式加载（调用 create_app 工厂）。
本地开发也可以 ``python -m flask --app wsgi run``。
"""
from __future__ import annotations

from app import create_app


def app():
    return create_app()
