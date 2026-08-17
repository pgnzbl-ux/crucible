"""Lab 配方 MinIO 打包 / 解压 / Service 存取。"""
import os
import sys
import tarfile
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

SHA = "a" * 40


def test_recipe_object_key_and_bucket():
    from app.contexts.lab.recipe_store import RECIPE_BUCKET, recipe_object_key

    assert RECIPE_BUCKET == "crucible-lab-recipe"
    assert recipe_object_key("u1", "p1", SHA) == f"recipe/u1/p1/{SHA}.tar.gz"


def test_pack_and_extract_roundtrip(tmp_path):
    from app.contexts.lab.recipe_store import extract_recipe, pack_recipe

    work = tmp_path / "lab"
    (work / ".vuln-env").mkdir(parents=True)
    (work / ".vuln-env" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    archive = tmp_path / "r.tar.gz"
    meta = {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {"protocol": "http"},
        "initial_creds": {"user": "a"},
        "started_containers": ["web"],
    }
    pack_recipe(str(work), str(archive), meta)
    names = tarfile.open(archive, "r:gz").getnames()
    assert any(n.endswith(".vuln-env/docker-compose.yml") or n == ".vuln-env/docker-compose.yml" for n in names)
    assert any(n.endswith("recipe-meta.json") or n == "recipe-meta.json" for n in names)

    dest = tmp_path / "out"
    dest.mkdir()
    got = extract_recipe(str(archive), str(dest))
    assert got["initial_creds"] == {"user": "a"}
    assert (dest / ".vuln-env" / "docker-compose.yml").is_file()


@pytest.mark.asyncio
async def test_download_missing_returns_none(tmp_path):
    from app.contexts.lab.recipe_store import MemoryRecipeStore
    from app.contexts.lab.service import LabService

    svc = LabService(MagicMock(), recipe_store=MemoryRecipeStore())
    assert await svc.download_recipe(
        owner_id="u1", project_id="p1", commit_sha=SHA, dest_workdir=str(tmp_path)
    ) is None


@pytest.mark.asyncio
async def test_upload_then_download_via_service(tmp_path):
    from app.contexts.lab.recipe_store import MemoryRecipeStore
    from app.contexts.lab.service import LabService

    store = MemoryRecipeStore()
    svc = LabService(MagicMock(), recipe_store=store)
    work = tmp_path / "lab"
    (work / ".vuln-env").mkdir(parents=True)
    (work / ".vuln-env" / "docker-compose.yml").write_text("services:\n  web: {}\n", encoding="utf-8")
    await svc.upload_recipe(
        owner_id="u1",
        project_id="p1",
        commit_sha=SHA,
        lab_workdir=str(work),
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"},
        initial_creds={},
        started_containers=["web"],
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    hit = await svc.download_recipe(
        owner_id="u1", project_id="p1", commit_sha=SHA, dest_workdir=str(dest)
    )
    assert hit is not None
    assert hit["compose_path"] == ".vuln-env/docker-compose.yml"
    assert (dest / ".vuln-env" / "docker-compose.yml").is_file()


def test_extract_missing_or_invalid_meta_returns_empty(tmp_path):
    import io

    from app.contexts.lab.recipe_store import extract_recipe

    work = tmp_path / "lab"
    (work / ".vuln-env").mkdir(parents=True)
    (work / ".vuln-env" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    empty_meta_archive = tmp_path / "empty-meta.tar.gz"
    with tarfile.open(empty_meta_archive, "w:gz") as tar:
        tar.add(work / ".vuln-env", arcname=".vuln-env")
    dest = tmp_path / "out"
    dest.mkdir()
    assert extract_recipe(str(empty_meta_archive), str(dest)) == {}

    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(work / ".vuln-env", arcname=".vuln-env")
        payload = b"{not-json"
        info = tarfile.TarInfo(name="recipe-meta.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    dest_bad = tmp_path / "out-bad"
    dest_bad.mkdir()
    assert extract_recipe(str(bad), str(dest_bad)) == {}


@pytest.mark.asyncio
async def test_download_missing_compose_returns_none(tmp_path):
    from app.contexts.lab.recipe_store import MemoryRecipeStore, pack_recipe, recipe_object_key
    from app.contexts.lab.service import LabService

    work = tmp_path / "lab"
    (work / ".vuln-env").mkdir(parents=True)
    (work / ".vuln-env" / "README.txt").write_text("no compose\n", encoding="utf-8")
    archive = tmp_path / "r.tar.gz"
    pack_recipe(str(work), str(archive), {})
    store = MemoryRecipeStore()
    store.upload(recipe_object_key("u1", "p1", SHA), str(archive))
    svc = LabService(MagicMock(), recipe_store=store)
    dest = tmp_path / "dest"
    dest.mkdir()
    assert await svc.download_recipe(
        owner_id="u1", project_id="p1", commit_sha=SHA, dest_workdir=str(dest)
    ) is None


@pytest.mark.asyncio
async def test_download_miss_does_not_dirty_dest(tmp_path):
    from app.contexts.lab.recipe_store import MemoryRecipeStore, pack_recipe, recipe_object_key
    from app.contexts.lab.service import LabService

    work = tmp_path / "lab"
    (work / ".vuln-env").mkdir(parents=True)
    (work / ".vuln-env" / "README.txt").write_text("no compose\n", encoding="utf-8")
    archive = tmp_path / "r.tar.gz"
    pack_recipe(str(work), str(archive), {})
    store = MemoryRecipeStore()
    store.upload(recipe_object_key("u1", "p1", SHA), str(archive))
    svc = LabService(MagicMock(), recipe_store=store)

    empty = tmp_path / "empty"
    empty.mkdir()
    assert await svc.download_recipe(
        owner_id="u1", project_id="p1", commit_sha=SHA, dest_workdir=str(empty)
    ) is None
    assert not (empty / ".vuln-env").exists()
    assert not (empty / "recipe-meta.json").exists()

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    keep = occupied / "keep.txt"
    keep.write_text("untouched\n", encoding="utf-8")
    (occupied / "other").mkdir()
    assert await svc.download_recipe(
        owner_id="u1", project_id="p1", commit_sha=SHA, dest_workdir=str(occupied)
    ) is None
    assert keep.read_text(encoding="utf-8") == "untouched\n"
    assert (occupied / "other").is_dir()
    assert not (occupied / ".vuln-env").exists()
    assert not (occupied / "recipe-meta.json").exists()


@pytest.mark.asyncio
async def test_download_applies_meta_defaults(tmp_path):
    from app.contexts.lab.recipe_store import MemoryRecipeStore, pack_recipe, recipe_object_key
    from app.contexts.lab.service import LabService

    work = tmp_path / "lab"
    (work / ".vuln-env").mkdir(parents=True)
    (work / ".vuln-env" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    archive = tmp_path / "r.tar.gz"
    pack_recipe(str(work), str(archive), {})
    store = MemoryRecipeStore()
    store.upload(recipe_object_key("u1", "p1", SHA), str(archive))
    svc = LabService(MagicMock(), recipe_store=store)
    dest = tmp_path / "dest"
    dest.mkdir()
    hit = await svc.download_recipe(
        owner_id="u1", project_id="p1", commit_sha=SHA, dest_workdir=str(dest)
    )
    assert hit == {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {},
        "initial_creds": {},
        "started_containers": [],
    }


@pytest.mark.asyncio
async def test_upload_missing_vuln_env_returns_without_storing(tmp_path):
    from app.contexts.lab.recipe_store import MemoryRecipeStore, recipe_object_key
    from app.contexts.lab.service import LabService

    store = MemoryRecipeStore()
    svc = LabService(MagicMock(), recipe_store=store)
    work = tmp_path / "lab"
    work.mkdir()
    await svc.upload_recipe(
        owner_id="u1",
        project_id="p1",
        commit_sha=SHA,
        lab_workdir=str(work),
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={},
        initial_creds={},
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    assert await svc.download_recipe(
        owner_id="u1", project_id="p1", commit_sha=SHA, dest_workdir=str(dest)
    ) is None
    with pytest.raises(FileNotFoundError):
        store.download(recipe_object_key("u1", "p1", SHA), str(tmp_path / "x.tar.gz"))


@pytest.mark.asyncio
async def test_upload_store_error_is_logged_not_raised(tmp_path):
    from app.contexts.lab.service import LabService

    class BoomStore:
        def upload(self, object_key, archive_path):
            raise RuntimeError("minio down")

        def download(self, object_key, dest_path):
            raise FileNotFoundError(object_key)

    svc = LabService(MagicMock(), recipe_store=BoomStore())
    work = tmp_path / "lab"
    (work / ".vuln-env").mkdir(parents=True)
    (work / ".vuln-env" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    await svc.upload_recipe(
        owner_id="u1",
        project_id="p1",
        commit_sha=SHA,
        lab_workdir=str(work),
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={},
        initial_creds={},
    )


def test_default_recipe_store_is_minio():
    from app.contexts.lab.recipe_store import MinioRecipeStore, default_recipe_store

    assert isinstance(default_recipe_store(), MinioRecipeStore)


def test_lab_service_session_only_still_constructs():
    from app.contexts.lab.service import LabService

    svc = LabService(MagicMock())
    assert svc.recipe_store is not None
