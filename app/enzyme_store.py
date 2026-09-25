"""具名酶动力学常数参数档的持久化。

* 落地为 JSON 文件，服务重启后仍可按名取用；
* 写入走"同目录临时文件 + os.replace 原子替换"，进程崩溃不会留下半截文件；
* 所有读写由一把可重入锁串行化，保证多请求并发下各请求的登记互不覆盖；
  纯计算本身无共享状态，天然相互独立。

参数档形态::

    {"name": ..., "vmax": ..., "km": ..., "ki": <可选默认抑制常数>,
     "description": <可选说明>}
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .errors import (
    EnzymeExistsError,
    EnzymeNotFoundError,
    StoreError,
)

# 示范酶：己糖激酶（葡萄糖磷酸化，生理浓度量级）。
# 取可手算复核的数：Km=0.1 mM，[S]=0.1 mM 时速率恰为 5（半饱和点）；
# Ki=0.05 mM，[I]=0.05 mM 时 Km_app=0.2 mM。
_HEXOKINASE: dict[str, object] = {
    "name": "hexokinase",
    "vmax": 10.0,
    "km": 0.1,
    "ki": 0.05,
    "description": (
        "己糖激酶示例（葡萄糖磷酸化，单位自洽即可）：Vmax=10、Km=0.1 mM、"
        "Ki=0.05 mM。半饱和锚点：[S]=0.1 时 v=5；竞争抑制锚点："
        "[I]=Ki=0.05 时 Km_app=0.2，Vmax_app=10 不变。"
    ),
}

DEFAULT_STORE_PATH = Path(
    os.environ.get("ENZYME_STORE_PATH", "/data/enzymes.json")
)


class EnzymeStore:
    """JSON 文件支持的酶参数档仓库。"""

    def __init__(self, path: str | os.PathLike[str] = DEFAULT_STORE_PATH) -> None:
        self._path = Path(path)
        # 可重入锁：保存登记结果时允许在已持锁的上下文中再次进入。
        self._lock = threading.RLock()
        self._profiles: dict[str, dict[str, Any]] = {}
        self._load_or_seed()

    # ------------------------------------------------------------------ 装载

    def _load_or_seed(self) -> None:
        with self._lock:
            if not self._path.exists():
                profiles = {"hexokinase": dict(_HEXOKINASE)}
                try:
                    self._write_locked(profiles)
                except OSError:
                    # 落地路径不可写（如本地只读环境）：内存中仍预置示范酶，
                    # 保证计算端点可用；登记类接口随后会暴露存储错误。
                    self._profiles = profiles
                else:
                    self._profiles = profiles
                return

            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StoreError(
                    f"酶参数档存储文件 {self._path} 无法读取或已损坏：{exc}"
                ) from exc
            if not isinstance(raw, dict):
                raise StoreError(
                    f"酶参数档存储文件 {self._path} 的顶层结构必须是对象。"
                )
            self._profiles = {str(name): dict(data) for name, data in raw.items()}

    def _write_locked(self, profiles: dict[str, dict[str, Any]]) -> None:
        """原子写入；调用方必须持有 ``self._lock``。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self._path.name, suffix=".tmp", dir=self._path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(profiles, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _persist_locked(self) -> None:
        try:
            self._write_locked(self._profiles)
        except OSError as exc:
            raise StoreError(f"酶参数档无法写入 {self._path}：{exc}") from exc

    # ------------------------------------------------------------------ 查询

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(profile) for profile in self._profiles.values()]

    def get(self, name: str) -> dict[str, Any]:
        with self._lock:
            profile = self._profiles.get(name)
            if profile is None:
                raise EnzymeNotFoundError(
                    f"未找到名为 {name!r} 的酶参数档，请先登记或直接在请求中"
                    "提供 vmax/km。",
                    field="enzyme",
                )
            return dict(profile)

    def exists(self, name: str) -> bool:
        with self._lock:
            return name in self._profiles

    # ------------------------------------------------------------------ 变更

    def create(self, profile: dict[str, Any], *, overwrite: bool = False) -> bool:
        """登记一个参数档。

        :returns: True 表示新建，False 表示覆盖了已有记录。
        """
        name = profile["name"]
        with self._lock:
            existed = name in self._profiles
            if existed and not overwrite:
                raise EnzymeExistsError(
                    f"名为 {name!r} 的酶参数档已存在；如确需覆盖请使用 "
                    f"PUT /enzymes/{name}。",
                    field="name",
                )
            self._profiles[name] = dict(profile)
            self._persist_locked()
            return not existed

    def delete(self, name: str) -> None:
        with self._lock:
            if name not in self._profiles:
                raise EnzymeNotFoundError(
                    f"未找到名为 {name!r} 的酶参数档，无法删除。", field="name"
                )
            del self._profiles[name]
            self._persist_locked()
