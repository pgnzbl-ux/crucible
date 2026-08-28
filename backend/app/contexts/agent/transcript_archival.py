"""Agent Transcript 全量转录日志归档与读取服务。

将容器产生的全量 JSONL 事件流保存至 MinIO / 本地对象池，实现冷热分流。
"""
from __future__ import annotations

import logging

from app.shared.object_store import (
    ObjectNotFoundError,
    ObjectRef,
    ObjectStoreError,
    build_ref,
    get_object_store,
)

logger = logging.getLogger(__name__)


def archive_node_transcript(
    task_id: str,
    run_id: str,
    node_key: str,
    owner_id: str,
    transcript_text: str,
) -> ObjectRef | None:
    """将节点的全量 JSONL 转录日志存入 MinIO 对象存储。"""
    if not transcript_text or not transcript_text.strip():
        return None
    try:
        store = get_object_store()
        ref = store.put(
            kind="transcript",
            owner_id=owner_id or "system",
            data=transcript_text.encode("utf-8"),
            content_type="application/x-jsonlines",
            task_id=task_id,
            run_id=run_id,
            node_key=node_key,
        )
        logger.info("Transcript 已成功归档至 MinIO: %s", ref.key)
        return ref
    except ObjectStoreError as e:
        logger.warning("Transcript MinIO 归档失败（降级忽略）: %s", e)
        return None
    except Exception as e:
        logger.warning("Transcript 归档未捕获异常: %s", e)
        return None


def get_node_transcript(
    task_id: str,
    run_id: str,
    node_key: str,
    owner_id: str,
) -> str | None:
    """从 MinIO 对象存储读取指定任务/节点的全量转录日志。"""
    try:
        ref = build_ref(
            kind="transcript",
            owner_id=owner_id or "system",
            task_id=task_id,
            run_id=run_id,
            node_key=node_key,
        )
        store = get_object_store()
        data = store.get(ref)
        return data.decode("utf-8", errors="replace")
    except ObjectNotFoundError:
        return None
    except ObjectStoreError as e:
        logger.warning("读取 Transcript 失败: %s", e)
        return None
    except Exception as e:
        logger.warning("读取 Transcript 异常: %s", e)
        return None
