"""节点 outcome / coverage 一等语义。"""
from app.contexts.agent.contracts.outcome import attach_outcome


def test_attach_outcome_scan_failed_is_degraded():
    out = attach_outcome(
        {"engine": "semgrep", "status": "failed", "finding_count": 0, "error": "boom"},
        status="failed",
        error="boom",
    )
    assert out["outcome"] == "degraded"
    assert out["coverage"]["scan_status"] == "failed"


def test_attach_outcome_scan_completed_is_success():
    out = attach_outcome(
        {"engine": "gitleaks", "status": "completed", "finding_count": 3},
        status="completed",
    )
    assert out["outcome"] == "success"
    assert out["coverage"]["finding_count"] == 3


def test_attach_outcome_env_degraded():
    out = attach_outcome(
        {"ok": False, "target_url": None, "error": "compose failed"},
        ok=False,
        error="compose failed",
    )
    assert out["outcome"] == "degraded"
    assert out["coverage"]["lab_ready"] is False
