"""本地源码包：zip/tar 安全解压、路径穿越拒绝、规范打包。"""
import io
import tarfile
import zipfile

import pytest

from app.contexts.project.source_upload import (
    ingest_source_archive,
    parse_source_locator,
    parse_upload_locator,
    sanitize_slug,
)


def _zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(name, data)
    return buf.getvalue()


def _tar_gz_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_parse_upload_locator():
    parsed = parse_upload_locator("upload://local/demo-abc123def456")
    assert parsed.host == "upload"
    assert parsed.project_key == "local/demo-abc123def456"
    assert parsed.repo_dirname == "demo-abc123def456"
    assert parsed.normalized == "upload://local/demo-abc123def456"


def test_parse_source_locator_routes_git_and_upload():
    git = parse_source_locator("https://github.com/acme/app.git")
    assert git.host == "github.com"
    upload = parse_source_locator("upload://local/app-aaa", source_type="local_upload")
    assert upload.host == "upload"


@pytest.mark.parametrize(
    "raw",
    ["", "https://github.com/a/b", "upload://github.com/a", "upload://local/../x"],
)
def test_parse_upload_locator_rejects_bad(raw):
    with pytest.raises(ValueError):
        parse_upload_locator(raw)


def test_sanitize_slug_strips_archive_suffix():
    assert sanitize_slug("My App.tar.gz") == "my-app"
    assert sanitize_slug("foo.ZIP") == "foo"


def test_ingest_zip_single_root_dir(tmp_path):
    data = _zip_bytes({"demo/README.md": "# hi", "demo/src/main.py": "print(1)\n"})
    ingested = ingest_source_archive(data, "demo.zip", workdir=str(tmp_path / "w"))
    assert ingested.repo_dirname == "demo"
    assert "README.md" in ingested.top_level
    assert ingested.locator.startswith("upload://local/demo-")
    names = tarfile.open(ingested.archive_path, "r:gz").getnames()
    assert any(n.endswith("demo/README.md") or n == "demo/README.md" for n in names)


def test_ingest_zip_loose_files_wraps_dirname(tmp_path):
    data = _zip_bytes({"README.md": "x", "app.py": "y"})
    ingested = ingest_source_archive(data, "acme-src.zip", workdir=str(tmp_path / "w"))
    assert ingested.repo_dirname == "acme-src"
    assert set(ingested.top_level) >= {"README.md", "app.py"}


def test_ingest_tar_gz(tmp_path):
    data = _tar_gz_bytes({"webapp/index.php": "<?php echo 1;"})
    ingested = ingest_source_archive(data, "webapp.tar.gz", workdir=str(tmp_path / "w"))
    assert ingested.repo_dirname == "webapp"
    assert ingested.sha256
    assert len(ingested.sha256) == 64


def test_ingest_rejects_zip_slip(tmp_path):
    data = _zip_bytes({"../evil.txt": "nope"})
    with pytest.raises(ValueError, match="非法压缩路径"):
        ingest_source_archive(data, "evil.zip", workdir=str(tmp_path / "w"))


def test_ingest_rejects_empty_zip(tmp_path):
    data = _zip_bytes({})
    with pytest.raises(ValueError, match="空"):
        ingest_source_archive(data, "empty.zip", workdir=str(tmp_path / "w"))


def test_ingest_skips_macosx_junk(tmp_path):
    data = _zip_bytes({
        "app/main.py": "print(1)\n",
        "__MACOSX/._main.py": "junk",
        "app/.DS_Store": "junk",
    })
    ingested = ingest_source_archive(data, "app.zip", workdir=str(tmp_path / "w"))
    names = tarfile.open(ingested.archive_path, "r:gz").getnames()
    assert not any("__MACOSX" in n or ".DS_Store" in n for n in names)
    assert any(n.endswith("main.py") for n in names)
