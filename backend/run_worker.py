"""
Crucible Celery Worker 启动入口。

不要用裸 `celery -A app.celery_app worker` 命令：
- 本文件确保 app 包路径正确、配置已加载
- worker_prefetch_multiplier=1 + task_acks_late 由 celery_app 统一配置

启动：
    python run_worker.py
"""
import os
import sys

# 确保从 backend 目录运行时能 import app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.celery_app import celery_app  # noqa: E402

if __name__ == "__main__":
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=info",
            "--concurrency=2",
            "--pool=solo",  # 沙箱 exec 为阻塞调用，solo 模式避免线程池竞争
        ]
    )
