"""对象存储契约：3 桶、6 kind、Memory 往返、黑名单、createbuckets 清单。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPOSE = Path(__file__).resolve().parents[2] / "infrastructure" / "docker-compose.yml"
SHA = "a" * 40


def test_physical_buckets_are_three():
    from app.shared.object_store import PHYSICAL_BUCKETS

    assert PHYSICAL_BUCKETS == ("crucible-durable", "crucible-task", "crucible-public")


def test_kind_registry_has_seven_kinds():
    from app.shared.object_store import KIND_REGISTRY

    assert set(KIND_REGISTRY) == {
        "source",
        "recipe",
        "evidence",
        "report",
        "node_run",
        "transcript",
        "avatar",
    }
    assert KIND_REGISTRY["source"].bucket == "crucible-durable"
    assert KIND_REGISTRY["recipe"].bucket == "crucible-durable"
    assert KIND_REGISTRY["evidence"].bucket == "crucible-task"
    assert KIND_REGISTRY["report"].bucket == "crucible-task"
    assert KIND_REGISTRY["node_run"].bucket == "crucible-task"
    assert KIND_REGISTRY["transcript"].bucket == "crucible-task"
    assert KIND_REGISTRY["avatar"].bucket == "crucible-public"
    assert KIND_REGISTRY["avatar"].writable is False
    assert KIND_REGISTRY["source"].presign is False
    assert KIND_REGISTRY["recipe"].presign is False
    assert KIND_REGISTRY["node_run"].presign is False
    assert KIND_REGISTRY["transcript"].presign is False
    assert KIND_REGISTRY["evidence"].presign is True
    assert KIND_REGISTRY["report"].presign is True
    assert KIND_REGISTRY["avatar"].presign is True


def test_createbuckets_only_mentions_three_current_buckets():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "local/crucible-durable" in text
    assert "local/crucible-task" in text
    assert "local/crucible-public" in text
    for old in (
        "crucible-artifacts",
        "crucible-evidence",
        "crucible-source",
        "crucible-lab-recipe",
        "crucible-node-failure",
    ):
        assert f"local/{old}" not in text


def test_memory_put_get_roundtrip():
    from app.shared.object_store import MemoryObjectStore

    store = MemoryObjectStore()
    ref = store.put(
        "recipe",
        "u1",
        b"tar-bytes",
        content_type="application/gzip",
        project_id="p1",
        sha=SHA,
    )
    assert ref.kind == "recipe"
    assert ref.bucket == "crucible-durable"
    assert ref.key == f"recipe/u1/p1/{SHA}.tar.gz"
    assert store.get(ref) == b"tar-bytes"
    assert store.exists(ref) is True


def test_source_key_includes_owner():
    from app.shared.object_store import build_ref

    ref = build_ref(
        "source",
        "owner-1",
        git_host="github.com",
        project_key="siteboon/claudecodeui",
        sha=SHA,
    )
    assert ref.bucket == "crucible-durable"
    assert ref.key == f"source/owner-1/github.com/siteboon/claudecodeui/{SHA}.tar.gz"


def test_forbidden_kinds_rejected():
    from app.shared.object_store import ForbiddenKindError, MemoryObjectStore

    store = MemoryObjectStore()
    for kind in ("secret", "credential", "env"):
        with pytest.raises(ForbiddenKindError):
            store.put(kind, "u1", b"x", content_type="text/plain")


def test_unknown_kind_rejected():
    from app.shared.object_store import MemoryObjectStore, UnknownKindError

    store = MemoryObjectStore()
    with pytest.raises(UnknownKindError):
        store.put("logs", "u1", b"x", content_type="text/plain")


def test_avatar_not_writable():
    from app.shared.object_store import KindNotWritableError, MemoryObjectStore

    store = MemoryObjectStore()
    with pytest.raises(KindNotWritableError):
        store.put("avatar", "u1", b"img", content_type="image/png")


def test_file_name_path_traversal_rejected():
    from app.shared.object_store import MemoryObjectStore, UnsafeKeyError

    store = MemoryObjectStore()
    with pytest.raises(UnsafeKeyError):
        store.put(
            "evidence",
            "u1",
            b"x",
            content_type="text/plain",
            task_id="t1",
            evidence_id="e1",
            file_name="../etc/passwd",
        )
    with pytest.raises(UnsafeKeyError):
        store.put(
            "evidence",
            "u1",
            b"x",
            content_type="text/plain",
            task_id="t1",
            evidence_id="e1",
            file_name="a/b.png",
        )


def test_ensure_buckets_only_creates_physical_three():
    from app.shared.object_store import PHYSICAL_BUCKETS, MinioObjectStore

    created: list[str] = []

    class FakeClient:
        def bucket_exists(self, name):
            return False

        def make_bucket(self, name):
            created.append(name)

    store = MinioObjectStore.__new__(MinioObjectStore)
    store._client = FakeClient()
    store.ensure_buckets()
    assert created == list(PHYSICAL_BUCKETS)


def test_source_presign_rejected():
    from app.shared.object_store import MemoryObjectStore, ObjectStoreError, build_ref

    store = MemoryObjectStore()
    ref = build_ref(
        "source",
        "u1",
        git_host="github.com",
        project_key="a/b",
        sha=SHA,
    )
    with pytest.raises(ObjectStoreError, match="不允许预签名"):
        store.presign(ref)


def test_missing_object_raises():
    from app.shared.object_store import MemoryObjectStore, ObjectNotFoundError, build_ref

    store = MemoryObjectStore()
    ref = build_ref("recipe", "u1", project_id="p1", sha=SHA)
    with pytest.raises(ObjectNotFoundError):
        store.get(ref)
