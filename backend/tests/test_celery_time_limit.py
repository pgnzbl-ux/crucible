"""Celery 与 Agent Runner 都不按总运行时长终止任务。"""
from app.core.config import get_settings


def test_task_time_limit_is_disabled():
    s = get_settings()
    assert s.celery_task_time_limit is None
    assert not hasattr(s, "agent_runner_timeout_seconds")
    assert not hasattr(s, "agent_run_hard_timeout_seconds")


def test_celery_conf_uses_derived_limit():
    from app.core.celery_app import create_celery_app

    app = create_celery_app()
    assert app.conf.task_time_limit is None
    assert app.conf.broker_transport_options["visibility_timeout"] == 7 * 24 * 60 * 60
