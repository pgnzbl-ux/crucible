"""社区 semgrep 规则树：本地 git clone，不登录、不拉 Registry。

scan_semgrep 的 --config 只允许本地目录。优先用你已经 clone 好的仓库
（SCANNER_SEMGREP_RULES_DIR 指向 clone 根，根下要有 python/ java/ 等）。
未配置且目录缺失时，worker 才 `git clone --depth 1` 到当前前缀
share/crucible-semgrep-rules/。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from app.core.scanners import ScannerInstallError

SEMGREP_RULES_REPO = "https://github.com/semgrep/semgrep-rules.git"
LANGUAGE_DIRS = ("python", "java", "javascript", "typescript", "go", "php")

# 旧 profile / 缓存里的 registry id → 本地目录；owasp 包由语言树覆盖，丢弃
_REGISTRY_ALIAS: dict[str, str | None] = {
    "p/python": "python",
    "p/trailofbits": "python",
    "p/java": "java",
    "p/javascript": "javascript",
    "p/golang": "go",
    "p/php": "php",
    "p/owasp-top-ten": None,
}


def local_config_names(configs: list[str]) -> list[str]:
    """profile.semgrep_configs → 本地目录名。丢弃 registry / URL / auto。"""
    out: list[str] = []
    for cfg in configs:
        name = cfg.strip()
        if name in _REGISTRY_ALIAS:
            mapped = _REGISTRY_ALIAS[name]
        elif name.startswith(("p/", "r/", "http://", "https://")) or name == "auto":
            mapped = None
        else:
            mapped = name
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def rules_dir(explicit: str = "") -> Path:
    if explicit:
        return Path(explicit)
    return Path(sys.prefix) / "share" / "crucible-semgrep-rules"


def _ready(dest: Path) -> bool:
    return dest.is_dir() and any((dest / lang).is_dir() for lang in LANGUAGE_DIRS)


def _git_clone(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", SEMGREP_RULES_REPO, str(dest)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise ScannerInstallError(
            f"git clone {SEMGREP_RULES_REPO} 失败: {detail[:2000]}"
        ) from exc


def ensure_rules(*, explicit: str = "", auto_install: bool = True) -> Path:
    """返回规则仓库根目录。已有 python/ 等子树则直接用（你自己 clone 的也算）。"""
    dest = rules_dir(explicit)
    if _ready(dest):
        return dest
    hint = (
        f"请 git clone {SEMGREP_RULES_REPO} 并把 SCANNER_SEMGREP_RULES_DIR 指到仓库根"
    )
    if explicit:
        raise ScannerInstallError(f"{dest} 不是有效 semgrep 规则树（需要 python/ 等子目录）。{hint}")
    if not auto_install:
        raise ScannerInstallError(f"本地 semgrep 规则不存在: {dest}。{hint}")
    _git_clone(dest)
    if not _ready(dest):
        raise ScannerInstallError(f"git clone 后仍未找到语言规则目录: {dest}")
    return dest
