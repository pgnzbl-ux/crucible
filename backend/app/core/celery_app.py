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
        task_soft_time_limit=settings.celery_task_soft_time_limit,
        worker_prefetch_multiplier=1,  # 一次只取一个任务，配合 acks_late 保证不丢
        broker_transport_options={
            # Redis 消息租约不是执行超时；取足够长以免长任务仍在运行时被重复投递。
            "visibility_timeout": 7 * 24 * 60 * 60,
        },
    )
    app.autodiscover_tasks(["app.contexts.agent"])
    return app


celery_app = create_celery_app()
