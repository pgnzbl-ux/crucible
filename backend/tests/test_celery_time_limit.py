"""Celery 任务级 wall-clock 上限（防挂死 Agent 永久占满 worker/槽位）。"""
from app.core.config import get_settings


def test_task_time_limit_is_enabled():
    s = get_settings()
    assert s.celery_task_time_limit == 4 * 60 * 60
    assert s.celery_task_soft_time_limit == 3 * 60 * 60 + 30 * 60
    assert s.celery_task_soft_time_limit < s.celery_task_time_limit


def test_celery_conf_uses_derived_limit():
    from app.core.celery_app import create_celery_app

    app = create_celery_app()
    assert app.conf.task_time_limit == 4 * 60 * 60
    assert app.conf.task_soft_time_limit == 3 * 60 * 60 + 30 * 60
    assert app.conf.broker_transport_options["visibility_timeout"] == 7 * 24 * 60 * 60
