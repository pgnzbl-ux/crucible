import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings


def test_worker_uses_prefork_with_hard_cap():
    from run_worker import worker_argv

    cap = get_settings().agent_runner_concurrency_limit
    argv = worker_argv()
    assert "--pool=prefork" in argv
    assert f"--concurrency={cap}" in argv
