"""Celery 硬限必须覆盖整个 6 节点链，而不是单个容器超时。"""
from app.core.config import get_settings


def test_task_time_limit_derives_from_run_hard_timeout():
    """task_time_limit 跟 run 硬顶 + 余量，绝不再绑单容器 1800s（回归 2026-08-19）。"""
    s = get_settings()
    assert s.celery_task_time_limit == s.agent_run_hard_timeout_seconds + 300
    assert s.celery_task_time_limit > s.agent_runner_timeout_seconds * 2


def test_celery_conf_uses_derived_limit():
    from app.core.celery_app import create_celery_app

    app = create_celery_app()
    s = get_settings()
    assert app.conf.task_time_limit == s.celery_task_time_limit
    assert app.conf.broker_transport_options["visibility_timeout"] >= s.celery_task_time_limit
