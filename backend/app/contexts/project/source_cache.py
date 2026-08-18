"""项目源码打包 / 解包；对象读写走 shared.object_store。"""
from __future__ import annotations

import logging
import tarfile
from pathlib import Path

from app.core.config import get_settings
from app.shared.object_store import KIND_REGISTRY, ObjectNotFoundError, build_ref, get_object_store

logger = logging.getLogger(__name__)

SOURCE_BUCKET = KIND_REGISTRY["source"].bucket


def source_object_key(owner_id: str, git_host: str, project_key: str, commit_sha: str) -> str:
    return build_ref(
        "source",
        owner_id,
        git_host=git_host,
        project_key=project_key,
        sha=commit_sha,
    ).key


def object_access_url(object_key: str) -> str:
    base = get_settings().s3_endpoint.rstrip("/")
    return f"{base}/{SOURCE_BUCKET}/{object_key}"


def pack_project_dir(project_dir: str, archive_path: str, arcname: str | None = None) -> None:
    name = arcname or Path(project_dir).name
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(project_dir, arcname=name)


def extract_source_archive(archive_path: str, host_workdir: str) -> None:
    with tarfile.open(archive_path, "r:gz") as tar:
        kwargs: dict = {}
        if hasattr(tarfile, "data_filter"):
            kwargs["filter"] = "data"
        tar.extractall(host_workdir, **kwargs)


class MemorySourceStore:
    """测试用内存 store，按 object_key 存 tar.gz。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, bytes]] = {}

    def upload(self, object_key: str, sha: str, archive_path: str) -> None:
        self._data[object_key] = (sha, Path(archive_path).read_bytes())

    def download(self, object_key: str, dest_path: str) -> None:
        item = self._data.get(object_key)
        if not item:
            raise FileNotFoundError(f"源码缓存不存在: {object_key}")
        Path(dest_path).write_bytes(item[1])

    def get_bytes(self, object_key: str | None) -> bytes | None:
        if not object_key:
            return None
        item = self._data.get(object_key)
        return item[1] if item else None


class MinioSourceStore:
    def upload(self, object_key: str, sha: str, archive_path: str) -> None:
        get_object_store().put_at(
            "source",
            object_key,
            Path(archive_path).read_bytes(),
            content_type="application/gzip",
        )

    def download(self, object_key: str, dest_path: str) -> None:
        try:
            data = get_object_store().get_at("source", object_key)
        except ObjectNotFoundError as exc:
            raise FileNotFoundError(f"源码缓存不存在: {object_key}") from exc
        Path(dest_path).write_bytes(data)
