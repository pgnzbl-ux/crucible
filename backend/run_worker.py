"""
Crucible Celery Worker 启动入口。

不要用裸 `celery -A app.celery_app worker` 命令：
- 本文件确保 app 包路径正确、配置已加载
- worker_prefetch_multiplier=1 + task_acks_late 由 celery_app 统一配置
- Linux 默认 `--pool=prefork`，进程数 = AGENT_RUNNER_CONCURRENCY_LIMIT

启动：
    python run_worker.py
"""
import os
import sys

# 确保从 backend 目录运行时能 import app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.celery_app import celery_app  # noqa: E402


def worker_argv() -> list[str]:
    from app.core.config import get_settings

    cap = get_settings().agent_runner_concurrency_limit
    return ["worker", "--loglevel=info", "--pool=prefork", f"--concurrency={cap}"]


if __name__ == "__main__":
    celery_app.worker_main(worker_argv())
