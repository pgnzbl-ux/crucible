"""节点一等 outcome / coverage 语义（discovery-spec 降级可观察）。

NodeRun.status 仍可为 completed（失败隔离）；业务健康度看 output.outcome。
"""
from __future__ import annotations

from typing import Any


def attach_outcome(
    payload: dict[str, Any],
    *,
    status: str | None = None,
    ok: bool | None = None,
    error: str | None = None,
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为节点 output 写入统一 outcome / coverage，不覆盖调用方已显式设置的字段。"""
    out = dict(payload)
    if "outcome" not in out:
        resolved_status = status or out.get("status")
        if ok is False or resolved_status == "failed" or (error or out.get("error")):
            out["outcome"] = "degraded"
        elif resolved_status == "skipped":
            out["outcome"] = "skipped"
        elif ok is True or resolved_status in ("completed", None):
            out["outcome"] = "success"
        else:
            out["outcome"] = "degraded"
    if "coverage" not in out:
        if coverage is not None:
            out["coverage"] = coverage
        else:
            default = _default_coverage(out)
            if default is not None:
                out["coverage"] = default
    return out


def _default_coverage(payload: dict[str, Any]) -> dict[str, Any] | None:
    if "finding_count" in payload or payload.get("engine"):
        return {
            "engine": payload.get("engine"),
            "finding_count": int(payload.get("finding_count") or 0),
            "scan_status": payload.get("status"),
        }
    if "target_url" in payload or "ok" in payload:
        return {
            "lab_ready": bool(payload.get("ok") is not False and payload.get("target_url")),
            "has_target_url": bool(payload.get("target_url")),
        }
    return None
