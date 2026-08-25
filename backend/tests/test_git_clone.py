"""git_clone_to_workdir：按 commit 检出不得把 SHA 当远端 ref 名去 fetch。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.agent_runner import git_clone_to_workdir


def _run(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True,
    )
    return (out.stdout or "").strip()


def _init_origin_with_history(root: Path) -> tuple[Path, str, str]:
    origin = root / "origin"
    origin.mkdir()
    _run(origin, "git", "init", "-b", "main")
    _run(origin, "git", "config", "user.email", "t@t.t")
    _run(origin, "git", "config", "user.name", "t")
    (origin / "a.txt").write_text("first\n", encoding="utf-8")
    _run(origin, "git", "add", ".")
    _run(origin, "git", "commit", "-m", "first")
    old = _run(origin, "git", "rev-parse", "HEAD")
    (origin / "a.txt").write_text("second\n", encoding="utf-8")
    _run(origin, "git", "add", ".")
    _run(origin, "git", "commit", "-m", "second")
    new = _run(origin, "git", "rev-parse", "HEAD")
    assert old != new
    return origin, old, new


def test_clone_commit_short_sha_checks_out_non_head(tmp_path):
    """浅克隆默认分支后再 fetch origin <短 SHA> 会报 couldn't find remote ref。"""
    origin, old, _new = _init_origin_with_history(tmp_path)
    workdir = tmp_path / "wd"
    workdir.mkdir()
    short = old[:7]

    ok, err = git_clone_to_workdir(
        str(workdir), str(origin), short, "repo",
        ref_type="commit", clone_depth=1,
    )
    assert ok, err
    head = _run(workdir / "repo", "git", "rev-parse", "HEAD")
    assert head == old
    assert (workdir / "repo" / "a.txt").read_text(encoding="utf-8") == "first\n"


def test_clone_commit_full_sha_not_on_shallow_head(tmp_path):
    origin, old, _new = _init_origin_with_history(tmp_path)
    workdir = tmp_path / "wd"
    workdir.mkdir()

    ok, err = git_clone_to_workdir(
        str(workdir), str(origin), old, "repo",
        ref_type="commit", clone_depth=1,
    )
    assert ok, err
    head = _run(workdir / "repo", "git", "rev-parse", "HEAD")
    assert head == old


def test_clone_commit_inferred_from_short_hex_without_explicit_type(tmp_path):
    origin, old, _new = _init_origin_with_history(tmp_path)
    workdir = tmp_path / "wd"
    workdir.mkdir()

    ok, err = git_clone_to_workdir(
        str(workdir), str(origin), old[:7], "repo", clone_depth=1,
    )
    assert ok, err
    assert _run(workdir / "repo", "git", "rev-parse", "HEAD") == old
