"""酶促动力学 HTTP 计算服务。

模块划分：

* :mod:`app.validation`    入参校验
* :mod:`app.kinetics`      米氏速率核心
* :mod:`app.inhibition`    竞争性抑制换算
* :mod:`app.enzyme_store`  酶参数档持久化
* :mod:`app.routes`        Flask 路由与应用工厂
"""
from .errors import (
    AppError,
    EnzymeExistsError,
    EnzymeNotFoundError,
    InhibitionError,
    StoreError,
    ValidationError,
)
from .kinetics import evaluate, michaelis_rate, saturation_fraction
from .inhibition import (
    SUPPORTED_INHIBITION_TYPE,
    apparent_km,
    competitive_factor,
    evaluate_competitive,
)
from .enzyme_store import EnzymeStore
from .routes import app, create_app

__all__ = [
    "AppError",
    "EnzymeExistsError",
    "EnzymeNotFoundError",
    "InhibitionError",
    "StoreError",
    "ValidationError",
    "SUPPORTED_INHIBITION_TYPE",
    "EnzymeStore",
    "app",
    "apparent_km",
    "competitive_factor",
    "create_app",
    "evaluate",
    "evaluate_competitive",
    "michaelis_rate",
    "saturation_fraction",
]
