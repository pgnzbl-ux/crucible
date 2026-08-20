"""源码缓存：按 project_key + commit SHA 打包到 MinIO。"""
import tarfile
from pathlib import Path

from app.contexts.project.source_cache import (
    SOURCE_BUCKET,
    pack_project_dir,
    source_object_key,
)


SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_source_bucket_is_platform_constant():
    assert SOURCE_BUCKET == "crucible-durable"


def test_source_object_key_uses_host_project_and_sha():
    assert source_object_key("owner-1", "github.com", "siteboon/claudecodeui", SHA_A) == (
        f"source/owner-1/github.com/siteboon/claudecodeui/{SHA_A}.tar.gz"
    )


def test_source_object_key_for_local_upload():
    sha = "b" * 64
    assert source_object_key("owner-1", "upload", "local/demo-aaa", sha) == (
        f"source/owner-1/upload/local/demo-aaa/{sha}.tar.gz"
    )


def test_pack_project_uses_repo_dirname_arcname(tmp_path):
    repo = tmp_path / "claudecodeui"
    repo.mkdir()
    (repo / "README.md").write_text("src", encoding="utf-8")
    (tmp_path / ".node.json").write_text("{}", encoding="utf-8")
    archive = tmp_path / "src.tar.gz"
    pack_project_dir(str(repo), str(archive), arcname="claudecodeui")

    names = tarfile.open(archive, "r:gz").getnames()
    assert any("claudecodeui/README.md" in n or n.endswith("README.md") for n in names)
    assert not any(n == "project" or n.startswith("project/") for n in names)
    assert not any(".node.json" in n for n in names)
