"""社区 semgrep 规则树：本地目录，不登录、不拉 Registry。

运行时真相：``SCANNER_SEMGREP_RULES_DIR``（默认 ``backend/semgrep_rules``）。
该目录下为各语言社区树（``php/`` ``python/`` …）+ ``crucible/`` 叠加包。

``scan_semgrep`` 对每个 profile 语言传入：

```text
--config {RULES_DIR}/{lang} --config {RULES_DIR}/crucible/{lang}
```

未配置且目录缺失时，worker 才 ``git clone --depth 1`` 到当前前缀
``share/crucible-semgrep-rules/``（无 crucible 叠加）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from app.core.scanners import ScannerInstallError

SEMGREP_RULES_REPO = "https://github.com/semgrep/semgrep-rules.git"
# 与 backend/semgrep_rules/<name>/、crucible/<name>/ 文件夹名必须一致（唯一白名单）
LANGUAGE_DIRS = ("python", "java", "javascript", "typescript", "go", "php")
ALLOWED_SEMGREP_LANG_DIRS = frozenset(LANGUAGE_DIRS)
_OVERLAY_UNDER_RULES = "crucible"


class SemgrepLangDirError(ValueError):
    """semgrep_configs / lang 与规则库目录名不一致。"""


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
    """profile.semgrep_configs → 本地目录名。

    只保留 ``ALLOWED_SEMGREP_LANG_DIRS``（与规则库文件夹同名）；
    丢弃 registry / URL / auto / 未知别名（如 ``nodejs``、``golang``）。
    """
    out: list[str] = []
    for cfg in configs:
        name = cfg.strip()
        if name in _REGISTRY_ALIAS:
            mapped = _REGISTRY_ALIAS[name]
        elif name.startswith(("p/", "r/", "http://", "https://")) or name == "auto":
            mapped = None
        elif name in ALLOWED_SEMGREP_LANG_DIRS:
            mapped = name
        else:
            mapped = None
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def require_allowed_lang_dirs(names: list[str]) -> list[str]:
    """Fail-Fast：任一名称不在白名单则抛错（扫描入口 / 派生表自检）。"""
    bad = [n for n in names if n not in ALLOWED_SEMGREP_LANG_DIRS]
    if bad:
        raise SemgrepLangDirError(
            f"semgrep 语言目录名非法: {bad}；"
            f"必须与规则库文件夹一致，允许: {sorted(ALLOWED_SEMGREP_LANG_DIRS)}"
        )
    return names


def default_bundle_dir() -> Path:
    """``backend/semgrep_rules``（本文件位于 ``backend/app/core/``）。"""
    return Path(__file__).resolve().parents[2] / "semgrep_rules"


def overlay_rules_dir(*, explicit: str = "", rules_dir: str = "") -> Path:
    """解析 Crucible 叠加规则根：``…/crucible``。

    优先级：``SCANNER_SEMGREP_OVERLAY_DIR`` → ``{RULES_DIR}/crucible``（若存在）
    → ``backend/semgrep_rules/crucible``。
    """
    if explicit:
        return Path(explicit)
    if rules_dir:
        under = Path(rules_dir) / _OVERLAY_UNDER_RULES
        if under.is_dir():
            return under
    bundled = default_bundle_dir() / _OVERLAY_UNDER_RULES
    if bundled.is_dir():
        return bundled
    if rules_dir:
        return Path(rules_dir) / _OVERLAY_UNDER_RULES
    return bundled


def overlay_config_paths(
    lang_names: list[str],
    *,
    explicit: str = "",
    rules_dir: str = "",
) -> list[str]:
    """对已扫描的语言，若 overlay 下存在同名目录则返回其绝对路径。"""
    root = overlay_rules_dir(explicit=explicit, rules_dir=rules_dir)
    paths: list[str] = []
    for name in lang_names:
        path = root / name
        if path.is_dir() and str(path) not in paths:
            paths.append(str(path))
    return paths


def rules_dir(explicit: str = "") -> Path:
    if explicit:
        return Path(explicit)
    bundled = default_bundle_dir()
    if _ready(bundled):
        return bundled
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
    """返回规则仓库根目录。已有 python/ 等子树则直接用。"""
    dest = rules_dir(explicit)
    if _ready(dest):
        return dest
    hint = (
        f"请将社区语言目录放到 backend/semgrep_rules/，"
        f"或 git clone {SEMGREP_RULES_REPO} 并把 SCANNER_SEMGREP_RULES_DIR 指到该根"
    )
    if explicit:
        raise ScannerInstallError(f"{dest} 不是有效 semgrep 规则树（需要 python/ 等子目录）。{hint}")
    if not auto_install:
        raise ScannerInstallError(f"本地 semgrep 规则不存在: {dest}。{hint}")
    _git_clone(dest)
    if not _ready(dest):
        raise ScannerInstallError(f"git clone 后仍未找到语言规则目录: {dest}")
    return dest
