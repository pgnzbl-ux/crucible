from celery import Celery

from .config import get_settings

settings = get_settings()


def create_celery_app() -> Celery:
    app = Celery(
        "crucible",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Shanghai",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_time_limit=settings.celery_task_time_limit,
        worker_prefetch_multiplier=1,  # 一次只取一个任务，配合 acks_late 保证不丢
        broker_transport_options={
            "visibility_timeout": max(3600, settings.celery_task_time_limit + 300),
        },
    )
    app.autodiscover_tasks(["app.contexts.agent"])
    return app


celery_app = create_celery_app()
