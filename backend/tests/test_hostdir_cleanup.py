"""任务失败时保留 host_workdir，方便对照源码与节点产物。"""
from app.contexts.agent.tasks import reset_host_workdir, should_retain_hostdir


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
