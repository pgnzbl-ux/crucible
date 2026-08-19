"""UTC 时间约定：naive datetime 一律视为 UTC，禁止按本地时解码。"""
from __future__ import annotations

from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat()


def utc_unix(value: datetime | None) -> float | None:
    """SQLite naive datetime 按 UTC 转 unix，避免东八区把 running 任务判超龄。"""
    if value is None:
        return None
    return as_utc(value).timestamp()
