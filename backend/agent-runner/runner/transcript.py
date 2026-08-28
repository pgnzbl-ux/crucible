"""执行留痕：transcript.jsonl 实时追加 + .node_meta.json 审计 sidecar。

两者都是宿主机映射卷上的文件协议（/workspace/.runner/<execution_id>/…），
backend 读取归档（冷存 MinIO）——这是平台与 runner 的数据交接面，不是业务。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent-runner.transcript")

# meta sidecar 字段截尾（防大对象写爆映射卷）
ASSISTANT_TEXT_MAX_CHARS = 8_000
META_PATH_ENV = "NODE_META_PATH"
TRANSCRIPT_PATH_ENV = "NODE_TRANSCRIPT_PATH"
OUTPUT_PATH_ENV = "NODE_OUTPUT_PATH"


def env_output_path(override: str | None = None) -> str:
    return override or os.environ.get(OUTPUT_PATH_ENV, "/workspace/.node_output.json")


def env_meta_path(override: str | None = None) -> str:
    return override or os.environ.get(META_PATH_ENV, "/workspace/.node_meta.json")


def env_transcript_path(override: str | None = None) -> str | None:
    return override or os.environ.get(TRANSCRIPT_PATH_ENV) or None


class TranscriptWriter:
    """逐事件追加 transcript.jsonl（防崩溃与 OOM，实时 flush）。

    写失败只记 warning 不抛出——留痕通道不应阻断执行主流程，但必须可观测
    （不再静默吞掉）。线程安全（hook 回调与主循环并发追加）。
    """

    def __init__(self, path: str | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._failed = False

    @property
    def failed(self) -> bool:
        return self._failed

    def append(self, event: dict[str, Any]) -> None:
        if not self._path:
            return
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                p = Path(self._path)
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
            except OSError as e:
                if not self._failed:
                    self._failed = True
                    logger.warning("transcript 写入失败（后续不再重复告警）: %s", e)


def build_run_meta(
    *,
    node_key: str,
    model: str,
    prompt: str,
    system_append: str | None,
) -> dict[str, Any]:
    """审计链 sidecar 骨架：真实 prompt/skill 回传 worker（全量审计）。"""
    return {
        "node_key": node_key,
        "model": model,
        "prompt": prompt,
        "system_append": system_append,
    }


def finalize_run_meta(
    meta: dict[str, Any],
    *,
    assistant_texts: list[str],
    completed_event: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并 usage/turns/cost 等终态字段并截尾 assistant 文本。"""
    if completed_event:
        for key in (
            "usage",
            "model_usage",
            "num_turns",
            "duration_ms",
            "total_cost_usd",
            "session_id",
        ):
            if completed_event.get(key) is not None:
                meta[key] = completed_event[key]
    meta["assistant_text"] = "\n".join(assistant_texts)[-ASSISTANT_TEXT_MAX_CHARS:]
    return meta


def write_meta_sidecar(meta: dict[str, Any], path: str | None = None) -> bool:
    """写 .node_meta.json；失败不阻断主流程，返回是否成功。"""
    try:
        Path(env_meta_path(path)).write_text(
            json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8"
        )
        return True
    except OSError as e:
        logger.warning("meta sidecar 写入失败: %s", e)
        return False
