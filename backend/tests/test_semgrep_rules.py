"""本地 semgrep 规则树：registry 别名映射、认 clone 目录（不联网）。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.scanners import ScannerInstallError
from app.core.semgrep_rules import (
    ALLOWED_SEMGREP_LANG_DIRS,
    SEMGREP_RULES_REPO,
    SemgrepLangDirError,
    ensure_rules,
    local_config_names,
    overlay_config_paths,
    overlay_rules_dir,
    require_allowed_lang_dirs,
)
from app.contexts.agent.profile_detector import SEMGREP_CONFIG_BY_LANGUAGE


def test_local_config_names_drops_registry_and_dedupes():
    assert local_config_names(["p/python", "p/trailofbits", "p/owasp-top-ten"]) == ["python"]
    assert local_config_names(["python", "p/java"]) == ["python", "java"]
    assert local_config_names(["auto", "p/default", "https://semgrep.dev/c/p/python"]) == []
    assert local_config_names(["javascript", "typescript"]) == ["javascript", "typescript"]


def test_local_config_names_rejects_aliases_that_are_not_folder_names():
    # 画像语言 id / 口语别名 ≠ 规则库文件夹名
    assert local_config_names(["nodejs", "golang", "golang"]) == []
    assert local_config_names(["nodejs", "javascript"]) == ["javascript"]


def test_require_allowed_lang_dirs_fail_fast():
    assert require_allowed_lang_dirs(["php", "go"]) == ["php", "go"]
    with pytest.raises(SemgrepLangDirError, match="非法"):
        require_allowed_lang_dirs(["php", "nodejs"])


def test_profile_semgrep_config_table_subset_of_rules_dirs():
    for lang_id, dirs in SEMGREP_CONFIG_BY_LANGUAGE.items():
        assert set(dirs) <= ALLOWED_SEMGREP_LANG_DIRS, (lang_id, dirs)


def test_ensure_rules_uses_cloned_repo_without_git(tmp_path: Path):
    (tmp_path / "python").mkdir()
    with patch("app.core.semgrep_rules._git_clone") as clone:
        root = ensure_rules(explicit=str(tmp_path), auto_install=True)
    clone.assert_not_called()
    assert root == tmp_path


def test_ensure_rules_rejects_explicit_dir_without_language_tree(tmp_path: Path):
    (tmp_path / "README.md").write_text("not a rules repo\n")
    with pytest.raises(ScannerInstallError, match="SCANNER_SEMGREP_RULES_DIR|不是有效"):
        ensure_rules(explicit=str(tmp_path), auto_install=True)


def test_ensure_rules_clones_default_dir_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "app.core.semgrep_rules.sys",
        type("S", (), {"prefix": str(tmp_path)})(),
    )
    # 避免命中仓库内 backend/semgrep_rules
    monkeypatch.setattr(
        "app.core.semgrep_rules.default_bundle_dir",
        lambda: tmp_path / "no-bundle",
    )

    def fake_clone(dest: Path) -> None:
        (dest / "python").mkdir(parents=True)

    with patch("app.core.semgrep_rules._git_clone", side_effect=fake_clone) as clone:
        root = ensure_rules(auto_install=True)
    clone.assert_called_once()
    assert root == tmp_path / "share" / "crucible-semgrep-rules"
    assert (root / "python").is_dir()
    assert SEMGREP_RULES_REPO.startswith("https://github.com/semgrep/semgrep-rules")


def test_overlay_rules_dir_points_at_backend_semgrep_rules_crucible():
    root = overlay_rules_dir()
    assert root.name == "crucible"
    assert root.parent.name == "semgrep_rules"
    assert (root / "php").is_dir()


def test_overlay_config_paths_only_for_scanned_langs():
    bundle = Path(__file__).resolve().parents[1] / "semgrep_rules"
    paths = overlay_config_paths(
        ["php", "python", "java"], rules_dir=str(bundle)
    )
    assert any(p.endswith("/php") or p.endswith("\\php") for p in paths)
    assert any(p.endswith("/python") or p.endswith("\\python") for p in paths)
    java_root = overlay_rules_dir(rules_dir=str(bundle)) / "java"
    assert java_root.is_dir(), "java overlay 须存在于 backend/semgrep_rules/crucible/java"
    assert any(
        p.rstrip("/\\").endswith("java") and "/crucible/" in p.replace("\\", "/")
        for p in paths
    ), "java overlay 目录存在时 overlay_config_paths 必须挂上 java"
    go_only = overlay_config_paths(["go"], rules_dir=str(bundle))
    assert all("/php" not in p.replace("\\", "/") for p in go_only)
    assert any(p.endswith("/go") or p.endswith("\\go") for p in go_only)
