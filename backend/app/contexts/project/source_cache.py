"""项目源码 MinIO 存取：key=source/{git_host}/{project_key}/{sha}.tar.gz。"""
from __future__ import annotations

import logging
import os
import shutil
import tarfile
from pathlib import Path

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings

logger = logging.getLogger(__name__)

SOURCE_BUCKET = "crucible-source"

_client: Minio | None = None


def source_object_key(git_host: str, project_key: str, commit_sha: str) -> str:
    host = (git_host or "unknown").strip()
    return f"source/{host}/{project_key}/{commit_sha}.tar.gz"


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


def _minio_client() -> Minio:
    global _client
    if _client is None:
        settings = get_settings()
        _client = Minio(
            settings.s3_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )
    return _client


def ensure_source_bucket() -> None:
    client = _minio_client()
    if not client.bucket_exists(SOURCE_BUCKET):
        client.make_bucket(SOURCE_BUCKET)


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
        ensure_source_bucket()
        _minio_client().fput_object(
            SOURCE_BUCKET,
            object_key,
            archive_path,
            content_type="application/gzip",
            metadata={"commit-sha": sha},
            part_size=5 * 1024 * 1024,
        )

    def download(self, object_key: str, dest_path: str) -> None:
        _minio_client().fget_object(SOURCE_BUCKET, object_key, dest_path)
