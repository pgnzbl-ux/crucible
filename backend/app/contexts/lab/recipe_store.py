"""Lab 配方打包 / 解包；对象读写走 shared.object_store。"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from app.shared.object_store import KIND_REGISTRY, ObjectNotFoundError, get_object_store

RECIPE_BUCKET = KIND_REGISTRY["recipe"].bucket


def recipe_object_key(owner_id: str, project_id: str, commit_sha: str) -> str:
    return f"recipe/{owner_id}/{project_id}/{commit_sha}.tar.gz"


def pack_recipe(lab_workdir: str, archive_path: str, meta: dict) -> None:
    vuln_env = Path(lab_workdir) / ".vuln-env"
    meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    with tarfile.open(archive_path, "w:gz") as tar:
        if vuln_env.is_dir():
            tar.add(str(vuln_env), arcname=".vuln-env")
        info = tarfile.TarInfo(name="recipe-meta.json")
        info.size = len(meta_bytes)
        tar.addfile(info, io.BytesIO(meta_bytes))


def extract_recipe(archive_path: str, dest_workdir: str) -> dict:
    with tarfile.open(archive_path, "r:gz") as tar:
        kwargs: dict = {}
        if hasattr(tarfile, "data_filter"):
            kwargs["filter"] = "data"
        tar.extractall(dest_workdir, **kwargs)
    meta_path = Path(dest_workdir) / "recipe-meta.json"
    if not meta_path.is_file():
        return {}
    try:
        decoded = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError, OSError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


class MemoryRecipeStore:
    """测试用内存 store，按 object_key 存 tar.gz。"""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def upload(self, object_key: str, archive_path: str) -> None:
        self._data[object_key] = Path(archive_path).read_bytes()

    def download(self, object_key: str, dest_path: str) -> None:
        item = self._data.get(object_key)
        if item is None:
            raise FileNotFoundError(f"配方不存在: {object_key}")
        Path(dest_path).write_bytes(item)


class MinioRecipeStore:
    def upload(self, object_key: str, archive_path: str) -> None:
        get_object_store().put_at(
            "recipe",
            object_key,
            Path(archive_path).read_bytes(),
            content_type="application/gzip",
        )

    def download(self, object_key: str, dest_path: str) -> None:
        try:
            data = get_object_store().get_at("recipe", object_key)
        except ObjectNotFoundError as exc:
            raise FileNotFoundError(f"配方不存在: {object_key}") from exc
        Path(dest_path).write_bytes(data)


def default_recipe_store() -> MinioRecipeStore:
    return MinioRecipeStore()
