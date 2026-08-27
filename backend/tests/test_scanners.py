"""Go 扫描器安装：版本锁定、sha256、解析路径（不联网）。"""
from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.scanners import (
    GITLEAKS_VERSION,
    ScannerInstallError,
    _unpack,
    artifacts,
    install_one,
    resolve,
)


def test_artifact_urls_use_gitleaks_x64_not_amd64():
    arts = artifacts()
    assert f"gitleaks_{GITLEAKS_VERSION}_linux_" in arts["gitleaks"].url
    assert "linux_amd64.tar.gz" not in arts["gitleaks"].url
    assert "osv-scanner_linux_" in arts["osv-scanner"].url


def test_unpack_rejects_bad_hash(tmp_path: Path):
    art = artifacts()["osv-scanner"]
    with pytest.raises(ScannerInstallError, match="sha256"):
        _unpack(art, b"not-the-binary", tmp_path / "osv-scanner")


def test_install_one_writes_executable(tmp_path: Path):
    payload = b"#!/bin/sh\necho ok\n"
    art = artifacts()["osv-scanner"]
    digest = hashlib.sha256(payload).hexdigest()
    fake = art.__class__(name=art.name, url=art.url, sha256=digest, archive=False)
    with patch("app.core.scanners.artifacts", return_value={"osv-scanner": fake}):
        with patch("app.core.scanners._fetch", return_value=payload):
            dest = install_one("osv-scanner", dest_dir=tmp_path)
    assert dest.read_bytes() == payload
    assert dest.stat().st_mode & 0o111


def test_install_gitleaks_from_tar(tmp_path: Path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"gitleaks-bin"
        info = tarfile.TarInfo(name="gitleaks")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    blob = buf.getvalue()
    art = artifacts()["gitleaks"]
    fake = art.__class__(
        name="gitleaks", url=art.url, sha256=hashlib.sha256(blob).hexdigest(), archive=True,
    )
    with patch("app.core.scanners.artifacts", return_value={"gitleaks": fake}):
        with patch("app.core.scanners._fetch", return_value=blob):
            dest = install_one("gitleaks", dest_dir=tmp_path)
    assert dest.read_bytes() == b"gitleaks-bin"


def test_resolve_prefers_configured_dir(tmp_path: Path):
    bin_path = tmp_path / "gitleaks"
    bin_path.write_text("x")
    bin_path.chmod(0o755)
    assert resolve("gitleaks", bin_dir=str(tmp_path), auto_install=False) == str(bin_path)


def test_resolve_without_install_returns_bare_name(tmp_path: Path):
    assert resolve("definitely-missing-scanner", bin_dir=str(tmp_path), auto_install=False) == (
        "definitely-missing-scanner"
    )


def test_resolve_falls_back_to_sys_prefix_bin(tmp_path: Path, monkeypatch):
    """PATH 不含 venv/bin 时，仍应找到当前前缀里 pip/worker 装好的二进制。"""
    exe = tmp_path / "bin" / "semgrep"
    exe.parent.mkdir()
    exe.write_text("x")
    exe.chmod(0o755)
    monkeypatch.setattr("app.core.scanners.shutil.which", lambda n: None)
    monkeypatch.setattr("app.core.scanners.sys", type("S", (), {"prefix": str(tmp_path), "platform": "linux"})())
    assert resolve("semgrep", bin_dir="", auto_install=False) == str(exe)


def test_resolve_does_not_github_install_unknown_names(tmp_path: Path, monkeypatch):
    """semgrep 是 pip 包，auto_install 不得按 Go 产物去 GitHub 下。"""
    monkeypatch.setattr("app.core.scanners.shutil.which", lambda n: None)
    monkeypatch.setattr(
        "app.core.scanners.sys",
        type("S", (), {"prefix": str(tmp_path), "platform": "linux"})(),
    )
    with patch("app.core.scanners.install_one") as inst:
        assert resolve("semgrep", auto_install=True) == "semgrep"
        inst.assert_not_called()


def test_semgrep_cli_starts_if_installed(tmp_path: Path):
    """已安装的 semgrep 必须能 --version（捕获 setuptools 82 干掉 pkg_resources 这类启动失败）。"""
    import os
    import subprocess

    path = resolve("semgrep", auto_install=False)
    if path == "semgrep" or not Path(path).is_file():
        pytest.skip("semgrep 未安装")
    env = {**os.environ, "HOME": str(tmp_path)}
    r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr or r.stdout
