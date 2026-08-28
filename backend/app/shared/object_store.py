"""平台唯一对象存储：3 物理桶 + kind 注册表。Context 禁止再 Minio()。"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings

BUCKET_DURABLE = "crucible-durable"
BUCKET_TASK = "crucible-task"
BUCKET_PUBLIC = "crucible-public"
PHYSICAL_BUCKETS: tuple[str, ...] = (BUCKET_DURABLE, BUCKET_TASK, BUCKET_PUBLIC)

FORBIDDEN_KINDS = frozenset({"secret", "credential", "env"})


@dataclass(frozen=True)
class KindSpec:
    bucket: str
    presign: bool
    writable: bool


KIND_REGISTRY: dict[str, KindSpec] = {
    "source": KindSpec(BUCKET_DURABLE, presign=False, writable=True),
    "recipe": KindSpec(BUCKET_DURABLE, presign=False, writable=True),
    "evidence": KindSpec(BUCKET_TASK, presign=True, writable=True),
    "report": KindSpec(BUCKET_TASK, presign=True, writable=True),
    "node_run": KindSpec(BUCKET_TASK, presign=False, writable=True),
    "transcript": KindSpec(BUCKET_TASK, presign=False, writable=True),
    "avatar": KindSpec(BUCKET_PUBLIC, presign=True, writable=False),
}


@dataclass(frozen=True)
class ObjectRef:
    kind: str
    bucket: str
    key: str


class ObjectStoreError(Exception):
    pass


class UnknownKindError(ObjectStoreError):
    pass


class ForbiddenKindError(ObjectStoreError):
    pass


class UnsafeKeyError(ObjectStoreError):
    pass


class ObjectNotFoundError(ObjectStoreError):
    pass


class KindNotWritableError(ObjectStoreError):
    pass


def _kind_spec(kind: str) -> KindSpec:
    if kind in FORBIDDEN_KINDS:
        raise ForbiddenKindError(f"禁止的 kind: {kind}")
    spec = KIND_REGISTRY.get(kind)
    if spec is None:
        raise UnknownKindError(f"未知 kind: {kind}")
    return spec


def _safe_id(value: str, name: str) -> str:
    raw = (value or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw.split("/"):
        raise UnsafeKeyError(f"非法 {name}")
    return raw


def _safe_host(value: str) -> str:
    raw = (value or "unknown").strip().replace("\\", "/")
    if not raw or ".." in raw.split("/") or "/" in raw:
        raise UnsafeKeyError("非法 git_host")
    return raw


def _safe_project_key(value: str) -> str:
    raw = (value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        raise UnsafeKeyError("非法 project_key")
    parts = [p for p in raw.split("/") if p]
    if not parts or len(parts) > 2:
        raise UnsafeKeyError("非法 project_key")
    return "/".join(parts)


def _safe_file_name(value: str) -> str:
    raw = (value or "").strip().replace("\\", "/")
    if not raw or raw in {".", ".."} or ".." in raw.split("/") or "/" in raw:
        raise UnsafeKeyError(f"非法 file_name: {value!r}")
    return raw


def build_ref(kind: str, owner_id: str, **parts: str) -> ObjectRef:
    spec = _kind_spec(kind)
    owner = _safe_id(owner_id, "owner_id")
    if kind == "source":
        host = _safe_host(str(parts.get("git_host") or ""))
        project_key = _safe_project_key(str(parts.get("project_key") or ""))
        sha = _safe_id(str(parts.get("sha") or ""), "sha")
        key = f"source/{owner}/{host}/{project_key}/{sha}.tar.gz"
    elif kind == "recipe":
        project_id = _safe_id(str(parts.get("project_id") or ""), "project_id")
        sha = _safe_id(str(parts.get("sha") or ""), "sha")
        key = f"recipe/{owner}/{project_id}/{sha}.tar.gz"
    elif kind == "evidence":
        task_id = _safe_id(str(parts.get("task_id") or ""), "task_id")
        evidence_id = _safe_id(str(parts.get("evidence_id") or ""), "evidence_id")
        file_name = _safe_file_name(str(parts.get("file_name") or ""))
        key = f"evidence/{owner}/{task_id}/{evidence_id}/{file_name}"
    elif kind == "report":
        task_id = _safe_id(str(parts.get("task_id") or ""), "task_id")
        report_id = _safe_id(str(parts.get("report_id") or ""), "report_id")
        key = f"report/{owner}/{task_id}/{report_id}/body.json"
    elif kind == "node_run":
        task_id = _safe_id(str(parts.get("task_id") or ""), "task_id")
        run_id = _safe_id(str(parts.get("run_id") or ""), "run_id")
        node_key = _safe_id(str(parts.get("node_key") or ""), "node_key")
        key = f"node_run/{owner}/{task_id}/{run_id}/{node_key}.tar.gz"
    elif kind == "transcript":
        task_id = _safe_id(str(parts.get("task_id") or ""), "task_id")
        run_id = _safe_id(str(parts.get("run_id") or ""), "run_id")
        node_key = _safe_id(str(parts.get("node_key") or ""), "node_key")
        key = f"transcript/{owner}/{task_id}/{run_id}/{node_key}.jsonl"
    elif kind == "avatar":
        key = f"avatar/{owner}/profile"
    else:
        raise UnknownKindError(f"未知 kind: {kind}")
    return ObjectRef(kind=kind, bucket=spec.bucket, key=key)


class ObjectStore(Protocol):
    def put(
        self,
        kind: str,
        owner_id: str,
        data: bytes,
        *,
        content_type: str,
        **parts: str,
    ) -> ObjectRef: ...

    def get(self, ref: ObjectRef) -> bytes: ...

    def exists(self, ref: ObjectRef) -> bool: ...

    def delete(self, ref: ObjectRef) -> None: ...

    def presign(self, ref: ObjectRef, expires_seconds: int = 3600) -> str: ...

    def put_file(
        self,
        kind: str,
        owner_id: str,
        path: str,
        *,
        content_type: str,
        **parts: str,
    ) -> ObjectRef: ...

    def get_file(self, ref: ObjectRef, dest_path: str) -> None: ...

    def put_at(
        self,
        kind: str,
        key: str,
        data: bytes,
        *,
        content_type: str,
    ) -> ObjectRef: ...

    def get_at(self, kind: str, key: str) -> bytes: ...

    def delete_prefix(self, kind: str, prefix: str) -> int: ...

    def delete_at(self, kind: str, key: str) -> None: ...


def _assert_writable(kind: str) -> KindSpec:
    spec = _kind_spec(kind)
    if not spec.writable:
        raise KindNotWritableError(f"kind={kind} 不可写入")
    return spec


def _assert_presign(ref: ObjectRef) -> KindSpec:
    spec = _kind_spec(ref.kind)
    if not spec.presign:
        raise ObjectStoreError(f"kind={ref.kind} 不允许预签名")
    return spec


def _assert_key_prefix(kind: str, key: str) -> None:
    if not key.startswith(f"{kind}/"):
        raise UnsafeKeyError(f"key 必须以 {kind}/ 开头")


def stored_kind(key: str) -> str | None:
    """按注册表前缀识别已归档对象的 kind；无法识别则 None。"""
    raw = (key or "").strip()
    for kind in KIND_REGISTRY:
        if raw.startswith(f"{kind}/"):
            return kind
    return None


def task_artifact_prefixes(owner_id: str, task_id: str) -> list[tuple[str, str]]:
    """任务级对象前缀（证据 / 报告 / 节点产物）。SARIF 不在此前缀下。"""
    owner = _safe_id(owner_id, "owner_id")
    tid = _safe_id(task_id, "task_id")
    return [
        ("evidence", f"evidence/{owner}/{tid}/"),
        ("report", f"report/{owner}/{tid}/"),
        ("node_run", f"node_run/{owner}/{tid}/"),
    ]


class MemoryObjectStore:
    """测试用：按 (bucket, key) 存 bytes。"""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], bytes] = {}

    def put(
        self,
        kind: str,
        owner_id: str,
        data: bytes,
        *,
        content_type: str,
        **parts: str,
    ) -> ObjectRef:
        _assert_writable(kind)
        ref = build_ref(kind, owner_id, **parts)
        self._data[(ref.bucket, ref.key)] = data
        return ref

    def put_at(
        self,
        kind: str,
        key: str,
        data: bytes,
        *,
        content_type: str,
    ) -> ObjectRef:
        spec = _assert_writable(kind)
        _assert_key_prefix(kind, key)
        ref = ObjectRef(kind=kind, bucket=spec.bucket, key=key)
        self._data[(ref.bucket, ref.key)] = data
        return ref

    def get(self, ref: ObjectRef) -> bytes:
        item = self._data.get((ref.bucket, ref.key))
        if item is None:
            raise ObjectNotFoundError(f"{ref.bucket}/{ref.key}")
        return item

    def get_at(self, kind: str, key: str) -> bytes:
        spec = _kind_spec(kind)
        return self.get(ObjectRef(kind=kind, bucket=spec.bucket, key=key))

    def exists(self, ref: ObjectRef) -> bool:
        return (ref.bucket, ref.key) in self._data

    def delete(self, ref: ObjectRef) -> None:
        self._data.pop((ref.bucket, ref.key), None)

    def delete_at(self, kind: str, key: str) -> None:
        spec = _kind_spec(kind)
        self.delete(ObjectRef(kind=kind, bucket=spec.bucket, key=key))

    def delete_prefix(self, kind: str, prefix: str) -> int:
        spec = _kind_spec(kind)
        _assert_key_prefix(kind, prefix)
        n = 0
        for bucket, key in list(self._data):
            if bucket == spec.bucket and key.startswith(prefix):
                del self._data[(bucket, key)]
                n += 1
        return n

    def presign(self, ref: ObjectRef, expires_seconds: int = 3600) -> str:
        _assert_presign(ref)
        return f"memory://{ref.bucket}/{ref.key}"

    def put_file(
        self,
        kind: str,
        owner_id: str,
        path: str,
        *,
        content_type: str,
        **parts: str,
    ) -> ObjectRef:
        return self.put(
            kind,
            owner_id,
            Path(path).read_bytes(),
            content_type=content_type,
            **parts,
        )

    def get_file(self, ref: ObjectRef, dest_path: str) -> None:
        Path(dest_path).write_bytes(self.get(ref))


class MinioObjectStore:
    def __init__(self) -> None:
        self._client: Minio | None = None

    def _minio(self) -> Minio:
        if self._client is None:
            settings = get_settings()
            self._client = Minio(
                settings.s3_endpoint.replace("http://", "").replace("https://", ""),
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
                secure=settings.s3_secure,
            )
        return self._client

    def ensure_buckets(self) -> None:
        client = self._minio()
        for bucket in PHYSICAL_BUCKETS:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)

    def put(
        self,
        kind: str,
        owner_id: str,
        data: bytes,
        *,
        content_type: str,
        **parts: str,
    ) -> ObjectRef:
        _assert_writable(kind)
        ref = build_ref(kind, owner_id, **parts)
        self._put_bytes(ref, data, content_type)
        return ref

    def put_at(
        self,
        kind: str,
        key: str,
        data: bytes,
        *,
        content_type: str,
    ) -> ObjectRef:
        spec = _assert_writable(kind)
        _assert_key_prefix(kind, key)
        ref = ObjectRef(kind=kind, bucket=spec.bucket, key=key)
        self._put_bytes(ref, data, content_type)
        return ref

    def _put_bytes(self, ref: ObjectRef, data: bytes, content_type: str) -> None:
        self.ensure_buckets()
        self._minio().put_object(
            ref.bucket,
            ref.key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def get(self, ref: ObjectRef) -> bytes:
        try:
            response = self._minio().get_object(ref.bucket, ref.key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
                raise ObjectNotFoundError(f"{ref.bucket}/{ref.key}") from exc
            raise ObjectStoreError(str(exc)) from exc

    def get_at(self, kind: str, key: str) -> bytes:
        spec = _kind_spec(kind)
        return self.get(ObjectRef(kind=kind, bucket=spec.bucket, key=key))

    def exists(self, ref: ObjectRef) -> bool:
        try:
            self._minio().stat_object(ref.bucket, ref.key)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
                return False
            raise ObjectStoreError(str(exc)) from exc

    def delete(self, ref: ObjectRef) -> None:
        try:
            self._minio().remove_object(ref.bucket, ref.key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
                return
            raise ObjectStoreError(str(exc)) from exc

    def delete_at(self, kind: str, key: str) -> None:
        spec = _kind_spec(kind)
        self.delete(ObjectRef(kind=kind, bucket=spec.bucket, key=key))

    def delete_prefix(self, kind: str, prefix: str) -> int:
        spec = _kind_spec(kind)
        _assert_key_prefix(kind, prefix)
        n = 0
        try:
            client = self._minio()
            if not client.bucket_exists(spec.bucket):
                return 0
            for obj in client.list_objects(spec.bucket, prefix=prefix, recursive=True):
                name = getattr(obj, "object_name", None)
                if not name:
                    continue
                client.remove_object(spec.bucket, name)
                n += 1
        except S3Error as exc:
            raise ObjectStoreError(str(exc)) from exc
        return n

    def presign(self, ref: ObjectRef, expires_seconds: int = 3600) -> str:
        _assert_presign(ref)
        try:
            return self._minio().presigned_get_object(
                ref.bucket,
                ref.key,
                expires=timedelta(seconds=expires_seconds),
            )
        except S3Error as exc:
            raise ObjectStoreError(f"预签名 URL 生成失败: {exc}") from exc

    def put_file(
        self,
        kind: str,
        owner_id: str,
        path: str,
        *,
        content_type: str,
        **parts: str,
    ) -> ObjectRef:
        return self.put(
            kind,
            owner_id,
            Path(path).read_bytes(),
            content_type=content_type,
            **parts,
        )

    def get_file(self, ref: ObjectRef, dest_path: str) -> None:
        Path(dest_path).write_bytes(self.get(ref))


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = MinioObjectStore()
    return _store


def set_object_store_for_tests(store: ObjectStore | None) -> None:
    global _store
    _store = store


def ensure_buckets() -> None:
    store = get_object_store()
    if isinstance(store, MinioObjectStore):
        store.ensure_buckets()
