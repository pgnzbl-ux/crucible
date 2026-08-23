"""gitleaks / osv-scanner：锁定版本，装进当前 Python 前缀的 bin（通常是 .venv/bin）。

semgrep 走 pip。这两个官方是 Go 二进制、PyPI 无正包。版本与 sha256 只写在这里。
worker 启动时确保一次；扫描节点解析路径。测试默认不联网（SCANNER_AUTO_INSTALL=false）。
"""
from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITLEAKS_VERSION = "8.24.3"
OSV_SCANNER_VERSION = "2.1.0"

# 官方 checksums；key = (name, linux_arch_token)
_SHA256 = {
    ("gitleaks", "x64"): "9991e0b2903da4c8f6122b5c3186448b927a5da4deef1fe45271c3793f4ee29c",
    ("gitleaks", "arm64"): "5f2edbe1f49f7b920f9e06e90759947d3c5dfc16f752fb93aaafc17e9d14cf07",
    ("osv-scanner", "amd64"): "0d1123af0173ba42eef84b4d1c04750e417296a8cf25440c72ba39b4ef0859e4",
    ("osv-scanner", "arm64"): "4cfbcb957983997fcfe1bd7f8b9f83d6e14eb2477b95de845df00cd110eb574e",
}


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    sha256: str
    archive: bool  # tar.gz 内含同名二进制


class ScannerInstallError(RuntimeError):
    pass


def _linux_arch() -> tuple[str, str]:
    """返回 (gitleaks_arch, osv_arch)。仅 Linux。"""
    if sys.platform != "linux":
        raise ScannerInstallError(f"扫描器只支持 Linux 宿主，当前 {sys.platform}")
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64", "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64", "arm64"
    raise ScannerInstallError(f"不支持的架构: {machine}")


def artifacts() -> dict[str, Artifact]:
    g_arch, o_arch = _linux_arch()
    return {
        "gitleaks": Artifact(
            name="gitleaks",
            url=(
                f"https://github.com/gitleaks/gitleaks/releases/download/"
                f"v{GITLEAKS_VERSION}/gitleaks_{GITLEAKS_VERSION}_linux_{g_arch}.tar.gz"
            ),
            sha256=_SHA256[("gitleaks", g_arch)],
            archive=True,
        ),
        "osv-scanner": Artifact(
            name="osv-scanner",
            url=(
                f"https://github.com/google/osv-scanner/releases/download/"
                f"v{OSV_SCANNER_VERSION}/osv-scanner_linux_{o_arch}"
            ),
            sha256=_SHA256[("osv-scanner", o_arch)],
            archive=False,
        ),
    }


def install_dir(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(sys.prefix) / "bin"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "crucible-scanners"})
    last: Exception | None = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except OSError as exc:
            last = exc
    raise ScannerInstallError(f"下载失败 {url}: {last}") from last


def _unpack(art: Artifact, blob: bytes, dest: Path) -> None:
    digest = hashlib.sha256(blob).hexdigest()
    if digest != art.sha256:
        raise ScannerInstallError(f"{art.name} sha256 不匹配: 期望 {art.sha256} 实际 {digest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if art.archive:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            member = tar.getmember(art.name)
            extracted = tar.extractfile(member)
            if extracted is None:
                raise ScannerInstallError(f"{art.name} 压缩包内无二进制")
            dest.write_bytes(extracted.read())
    else:
        dest.write_bytes(blob)
    dest.chmod(0o755)


def install_one(name: str, *, dest_dir: Path) -> Path:
    art = artifacts()[name]
    dest = dest_dir / name
    if dest.is_file() and os.access(dest, os.X_OK):
        return dest
    _unpack(art, _fetch(art.url), dest)
    return dest


def ensure_installed(*, dest_dir: Path | None = None) -> dict[str, Path]:
    target = dest_dir or install_dir()
    return {name: install_one(name, dest_dir=target) for name in ("gitleaks", "osv-scanner")}


_GITHUB_BINARIES = frozenset({"gitleaks", "osv-scanner"})


def resolve(name: str, *, bin_dir: str = "", auto_install: bool = True) -> str:
    """解析可执行路径：配置目录 → PATH → 当前前缀 bin →（可选）GitHub 锁定安装。

    semgrep 等 pip 包只走前三步；auto_install 仅对 gitleaks / osv-scanner 生效。
    """
    if bin_dir:
        custom = Path(bin_dir) / name
        if custom.is_file():
            return str(custom)
    found = shutil.which(name)
    if found:
        return found
    prefix_bin = install_dir() / name
    if prefix_bin.is_file() and os.access(prefix_bin, os.X_OK):
        return str(prefix_bin)
    if auto_install and name in _GITHUB_BINARIES:
        return str(install_one(name, dest_dir=install_dir(bin_dir or None)))
    return name  # 交给 subprocess，缺失时 FileNotFoundError → 引擎失败隔离


def main() -> int:
    paths = ensure_installed()
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
