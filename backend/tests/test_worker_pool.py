import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings


def test_windows_worker_is_solo():
    from run_worker import worker_argv

    with patch("sys.platform", "win32"):
        argv = worker_argv()
    assert "--pool=solo" in argv
    assert "--concurrency=2" not in argv


def test_linux_worker_is_prefork_with_hard_cap():
    from run_worker import worker_argv

    cap = get_settings().agent_runner_concurrency_limit
    with patch("sys.platform", "linux"):
        argv = worker_argv()
    assert "--pool=prefork" in argv
    assert f"--concurrency={cap}" in argv
