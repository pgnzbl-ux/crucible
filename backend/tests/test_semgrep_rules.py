"""本地 semgrep 规则树：registry 别名映射、认 clone 目录（不联网）。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.scanners import ScannerInstallError
from app.core.semgrep_rules import (
    SEMGREP_RULES_REPO,
    ensure_rules,
    local_config_names,
)


def test_local_config_names_drops_registry_and_dedupes():
    assert local_config_names(["p/python", "p/trailofbits", "p/owasp-top-ten"]) == ["python"]
    assert local_config_names(["python", "p/java"]) == ["python", "java"]
    assert local_config_names(["auto", "p/default", "https://semgrep.dev/c/p/python"]) == []
    assert local_config_names(["javascript", "typescript"]) == ["javascript", "typescript"]


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

    def fake_clone(dest: Path) -> None:
        (dest / "python").mkdir(parents=True)

    with patch("app.core.semgrep_rules._git_clone", side_effect=fake_clone) as clone:
        root = ensure_rules(auto_install=True)
    clone.assert_called_once()
    assert root == tmp_path / "share" / "crucible-semgrep-rules"
    assert (root / "python").is_dir()
    assert SEMGREP_RULES_REPO.startswith("https://github.com/semgrep/semgrep-rules")
