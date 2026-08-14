"""
对象存储封装 — MinIO (S3 兼容)。

职责：
- 证据文件上传/下载/删除
- 报告产物归档
- 预签名 URL 生成（前端直读）

Bucket 约定（由 createbuckets 容器初始化）：
- crucible-artifacts  报告等结构化产物
- crucible-evidence   原始证据文件（扫描日志、复现输出）
- crucible-source     项目源码 tar.gz（见 project/source_cache.py）
"""

from __future__ import annotations

import io
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings

settings = get_settings()

ARTIFACTS_BUCKET = "crucible-artifacts"
EVIDENCE_BUCKET = "crucible-evidence"

_client: Minio | None = None


def get_client() -> Minio:
    """懒加载 MinIO 客户端"""
    global _client
    if _client is None:
        _client = Minio(
            settings.s3_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )
    return _client


class StorageError(Exception):
    pass


def ensure_buckets() -> None:
    """幂等创建 bucket（兜底，正常由 createbuckets 容器初始化）"""
    client = get_client()
    for bucket in (ARTIFACTS_BUCKET, EVIDENCE_BUCKET):
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)


def upload_evidence(
    key: str,
    data: bytes | BinaryIO,
    content_type: str = "application/octet-stream",
    *,
    task_id: str = "",
) -> str:
    """上传证据文件，返回对象 key。

    key 约定: {task_id}/{uuid}/{filename}
    """
    client = get_client()
    length: int | None = None
    if isinstance(data, bytes):
        length = len(data)
        data = io.BytesIO(data)
    try:
        client.put_object(
            EVIDENCE_BUCKET,
            key,
            data,
            length=length or 0,
            content_type=content_type,
        )
    except S3Error as e:
        raise StorageError(f"证据上传失败: {e}") from e
    return key


def upload_artifact(
    key: str,
    data: bytes | BinaryIO,
    content_type: str = "application/json",
) -> str:
    """上传报告产物到 artifacts bucket"""
    client = get_client()
    if isinstance(data, bytes):
        data = io.BytesIO(data)
    try:
        client.put_object(
            ARTIFACTS_BUCKET,
            key,
            data,
            length=data.getbuffer().nbytes if hasattr(data, "getbuffer") else 0,
            content_type=content_type,
        )
    except S3Error as e:
        raise StorageError(f"产物上传失败: {e}") from e
    return key


def get_object(bucket: str, key: str) -> bytes:
    """下载对象内容"""
    client = get_client()
    try:
        response = client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
    except S3Error as e:
        raise StorageError(f"对象读取失败: {e}") from e


def presigned_url(bucket: str, key: str, expires_seconds: int = 3600) -> str:
    """生成预签名 URL（前端直接访问）"""
    client = get_client()
    try:
        return client.presigned_get_object(bucket, key, expires=expires_seconds)
    except S3Error as e:
        raise StorageError(f"预签名 URL 生成失败: {e}") from e


def delete_object(bucket: str, key: str) -> None:
    client = get_client()
    try:
        client.remove_object(bucket, key)
    except S3Error as e:
        raise StorageError(f"对象删除失败: {e}") from e
