"""合格线索门表驱动（discovery-spec §2.7）：T2/传播/无证据/路径/OSV 不得入队。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.contexts.finding.qualify import (
    is_qualified_lead,
    rejection_reason,
    user_verdict_label,
)


def _group(**kw):
    base = dict(
        ai_verdict="tp",
        verdict_source="agent",
        ai_confidence=0.9,
        file_path="app/db.py",
        cwe="CWE-89",
        engine_set=["semgrep"],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _adj(**kw):
    qualify = kw.pop("qualify", {
        "attacker_controlled": True,
        "reaches_sink": True,
        "sanitizer": "none",
    })
    return SimpleNamespace(
        why=kw.pop("why", ["拼接注入"]),
        evidence=kw.pop("evidence", [{"file": "app/db.py", "lines": "2-2"}]),
        context_log=[{"qualify": qualify}] if qualify is not None else [],
        **kw,
    )


def _rep(**kw):
    return SimpleNamespace(engine=kw.get("engine", "semgrep"), raw=kw.get("raw") or {})


@pytest.mark.parametrize(
    "kwargs,ok",
    [
        ({}, True),
        ({"group": {"ai_verdict": "fp"}}, False),
        ({"group": {"ai_verdict": "need_more_context"}}, False),
        ({"group": {"verdict_source": "fast_model"}}, False),
        ({"group": {"verdict_source": "propagated"}}, False),
        ({"group": {"ai_confidence": 0.5}}, False),
        ({"adj": {"why": [], "qualify": {
            "attacker_controlled": True, "reaches_sink": True, "sanitizer": "none",
        }}}, False),
        ({"adj": {"evidence": [], "qualify": {
            "attacker_controlled": True, "reaches_sink": True, "sanitizer": "none",
        }}}, False),
        ({"adj": {"qualify": {
            "attacker_controlled": False, "reaches_sink": True, "sanitizer": "none",
        }}}, False),
        ({"adj": {"qualify": {
            "attacker_controlled": True, "reaches_sink": True, "sanitizer": "effective",
        }}}, False),
        ({"group": {"file_path": "tests/xss.js", "cwe": "CWE-79"}}, False),
        ({"group": {
            "file_path": "tests/secrets.py", "cwe": "CWE-798", "engine_set": ["gitleaks"],
        }}, True),
        ({"group": {"engine_set": ["osv"], "cwe": None},
          "rep": {"engine": "osv", "raw": {"called": False}}}, False),
        ({"group": {"engine_set": ["osv"], "cwe": None},
          "rep": {"engine": "osv", "raw": {"called": True}}}, True),
    ],
)
def test_qualify_gate_table(kwargs, ok):
    group = _group(**kwargs.get("group", {}))
    adj = _adj(**kwargs.get("adj", {}))
    rep = _rep(**kwargs.get("rep", {}))
    assert is_qualified_lead(
        group, representative=rep, adjudication=adj, high_confidence=0.8,
    ) is ok
    reason = rejection_reason(
        group, representative=rep, adjudication=adj, high_confidence=0.8,
    )
    assert (reason is None) is ok


def test_user_facing_labels_never_echo_tp_fp():
    assert user_verdict_label("tp") == "可疑真洞"
    assert user_verdict_label("fp") == "误报"
    assert user_verdict_label("need_more_context") == "二审未决"
    for code in ("tp", "fp", None, "need_more_context", "garbage"):
        text = user_verdict_label(code)
        assert text not in ("tp", "fp")
        assert "tp" not in text
        assert "fp" not in text
