"""项目源码打包 / 解包；对象读写走 shared.object_store。"""
from __future__ import annotations

import logging
import os
import tarfile
from pathlib import Path

from app.core.config import get_settings
from app.shared.object_store import (
    KIND_REGISTRY,
    ObjectNotFoundError,
    ObjectRef,
    build_ref,
    get_object_store,
)

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


def upload_source_object_key(owner_id: str, project_id: str) -> str:
    """上传包原始对象：一项目一份，不按内容 SHA 复用。"""
    return source_object_key(owner_id, "upload", project_id, "original")


def object_access_url(object_key: str) -> str:
    base = get_settings().s3_endpoint.rstrip("/")
    return f"{base}/{SOURCE_BUCKET}/{object_key}"


def pack_project_dir(project_dir: str, archive_path: str, arcname: str | None = None) -> None:
    name = arcname or Path(project_dir).name
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(project_dir, arcname=name)


def _source_member_filter(member: tarfile.TarInfo, destination: str) -> tarfile.TarInfo | None:
    """安全解包，并让缓存内容归当前 worker 所有且保持可清理。

    靶场容器可能把 bind mount 中的文件改成自己的 uid/gid；这些元数据若原样
    进入 tar，再由 root worker 解开，会制造 worker 无法覆盖/删除的 nobody 文件。
    """
    filtered = tarfile.data_filter(member, destination)
    if filtered is None:
        return None
    mode = filtered.mode if filtered.mode is not None else (member.mode or 0)
    if filtered.isdir():
        mode |= 0o700
    elif filtered.isfile():
        mode |= 0o600
    return filtered.replace(
        uid=os.getuid(),
        gid=os.getgid(),
        uname=None,
        gname=None,
        mode=mode,
    )


def extract_source_archive(archive_path: str, host_workdir: str) -> None:
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(host_workdir, filter=_source_member_filter)


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

    def delete(self, object_key: str) -> None:
        self._data.pop(object_key, None)


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

    def delete(self, object_key: str) -> None:
        spec = KIND_REGISTRY["source"]
        get_object_store().delete(ObjectRef(kind="source", bucket=spec.bucket, key=object_key))
