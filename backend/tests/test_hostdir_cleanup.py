"""任务失败时保留 host_workdir，方便对照源码与节点产物。"""
import os
from unittest.mock import patch

from app.contexts.agent.tasks import (
    _purge_secrets_dir,
    reset_host_workdir,
    should_retain_hostdir,
)


def test_retain_hostdir_on_failed_or_cancelled():
    assert should_retain_hostdir("failed") is True
    assert should_retain_hostdir("cancelled") is True
    assert should_retain_hostdir("needs_review") is True


def test_cleanup_hostdir_on_success():
    assert should_retain_hostdir("completed") is False
    assert should_retain_hostdir(None) is False


def test_reset_host_workdir_wipes_stub_project(tmp_path):
    stub = tmp_path / "project"
    stub.mkdir()
    (stub / "package.json").write_text("{}", encoding="utf-8")
    reset_host_workdir(str(tmp_path))
    assert tmp_path.is_dir()
    assert not stub.exists()
    assert list(tmp_path.iterdir()) == []


def test_reset_host_workdir_rotates_tree_when_inner_files_cannot_be_deleted(tmp_path):
    """靶场改属主后即使旧树删不掉，新 run 仍必须得到干净工作区。"""
    old = tmp_path / "repo" / "www"
    old.mkdir(parents=True)
    (old / ".htaccess").write_text("old", encoding="utf-8")

    with patch("app.contexts.agent.tasks._cleanup_hostdir", return_value=False):
        reset_host_workdir(str(tmp_path))

    assert tmp_path.is_dir()
    assert list(tmp_path.iterdir()) == []
    stale = list(tmp_path.parent.glob(f"{tmp_path.name}.stale-*"))
    assert len(stale) == 1
    assert (stale[0] / "repo" / "www" / ".htaccess").read_text() == "old"


def test_purge_secrets_dir_before_retain(tmp_path):
    """失败保留目录前必须删 .secrets/（明文凭据不留排查现场）。"""
    secret = tmp_path / ".secrets" / "tls.key"
    secret.parent.mkdir(parents=True)
    secret.write_text("SECRET", encoding="utf-8")
    marker = tmp_path / "keep.txt"
    marker.write_text("排查线索", encoding="utf-8")

    _purge_secrets_dir(str(tmp_path))

    assert not secret.parent.exists()
    assert marker.exists()


def test_purge_secrets_dir_noop_when_absent(tmp_path):
    _purge_secrets_dir(str(tmp_path))
    assert os.listdir(tmp_path) == []
    _purge_secrets_dir(str(tmp_path / "not-exist"))
