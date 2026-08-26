"""节点交接契约 — Handoff 投影 / Input 组装 / ControlSignals。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contexts.agent.contracts import (
    DEFAULT_PIPELINE,
    HandoffStore,
    InputAssembler,
    SkipWhen,
    SourceHandoff,
    TaskScalars,
    audit_for_reproduce,
    project_handoff,
)


def test_project_handoff_drops_private_keys():
    raw = {
        "commit_sha": "abc",
        "repo_dirname": "demo",
        "workspace_path": "/workspace/demo",
        "project_path": "/tmp/demo",
        "git_host": "github.com",
        "file_count": 99,
    }
    h = project_handoff("source", raw)
    dumped = h.model_dump(exclude_none=True)
    assert dumped["commit_sha"] == "abc"
    assert "git_host" not in dumped
    assert "file_count" not in dumped


def test_reproduce_input_strips_audit_defense_layers():
    """平台组装 reproduce Input 时不得把 defense_layers 等私有键塞进 audit。"""
    store = HandoffStore()
    store.set(
        "source",
        {"commit_sha": "a", "repo_dirname": "r", "workspace_path": "/workspace/r"},
    )
    store.set(
        "env_ready",
        {
            "target_url": "http://127.0.0.1:8080",
            "compose_path": ".vuln-env/docker-compose.yml",
            "initial_creds": {},
            "transport_shape": {"protocol": "http"},
        },
    )
    store.set(
        "audit",
        {
            "gate_verdict": "pass",
            "gate_reason": "ok",
            "core_claim": "sqli",
            "payloads": [{"method": "GET", "path": "/x", "expected_observable": "err"}],
            "runtime_dependent": False,
            "defense_layers": ["waf", "orm"],
            "extra_noise": True,
        },
    )
    inp = InputAssembler.assemble(
        "reproduce",
        store,
        TaskScalars(
            project_address="https://a/b",
            project_ref=None,
            project_ref_type=None,
            clone_depth=1,
            source_type="git",
            vulnerability_description="SQL injection in login form",
            host_workdir="/tmp",
            source_path="/tmp",
        ),
    )
    audit = InputAssembler.dump_for_persistence(inp)["audit"]
    assert audit["core_claim"] == "sqli"
    assert "defense_layers" not in audit
    assert "extra_noise" not in audit


def test_audit_for_reproduce_is_subset():
    raw = {
        "gate_verdict": "pass",
        "gate_reason": "ok",
        "core_claim": "sqli",
        "payloads": [{"method": "GET", "path": "/x"}],
        "runtime_dependent": False,
        "defense_layers": ["waf"],  # 不进 reproduce 子集
        "extra_private": "nope",
    }
    subset = audit_for_reproduce(raw).model_dump(exclude_none=True)
    assert subset["gate_verdict"] == "pass"
    assert subset["core_claim"] == "sqli"
    assert "defense_layers" not in subset
    assert "extra_private" not in subset


def test_input_assembler_forbid_unknown_on_source():
    with pytest.raises(ValidationError):
        from app.contexts.agent.contracts import SourceInput

        SourceInput(
            project_address="https://x/y",
            host_workdir="/tmp",
            source_path="/tmp",
            unexpected=True,
        )


def test_assemble_profile_from_store():
    store = HandoffStore()
    store.set(
        "source",
        {
            "commit_sha": "deadbeef",
            "repo_dirname": "app",
            "workspace_path": "/workspace/app",
            "project_path": "/host/app",
            "noise": 1,
        },
    )
    inp = InputAssembler.assemble(
        "profile",
        store,
        TaskScalars(
            project_address="https://a/b",
            project_ref="main",
            project_ref_type="branch",
            clone_depth=1,
            source_type="git",
            vulnerability_description="x" * 20,
            host_workdir="/host",
            source_path="/host",
        ),
    )
    assert inp.source.commit_sha == "deadbeef"
    assert inp.source_path == "/host/app"
    dumped = InputAssembler.dump_for_persistence(inp)
    assert "noise" not in dumped["source"]


def test_control_signals_non_web_and_gate():
    store = HandoffStore()
    # 未知 is_web 不得当成 non_web（否则会误 skip 靶场）
    assert store.signals().non_web is False
    store.set("profile", {"is_web": True, "language": "php"})
    assert store.signals().non_web is False
    store.set("profile", {"is_web": False, "language": "python"})
    assert store.signals().non_web is True
    store.set("audit", {"gate_verdict": "fail"})
    assert store.signals().gate_verdict == "fail"


def test_default_pipeline_skip_when_covers_branches():
    from app.contexts.agent.contracts import NODE_BY_KEY, VERIFY_PIPELINE

    by_key = {s.key: s for s in DEFAULT_PIPELINE}
    assert SkipWhen.NON_WEB in by_key["env_ready"].skip_when
    assert SkipWhen.NO_DISPATCH_LEAD in by_key["lead_verify"].skip_when
    assert "audit" not in by_key and "reproduce" not in by_key
    # gate 出口只在 verify 子图的 reproduce 上
    verify_repro = NODE_BY_KEY["reproduce"]
    assert SkipWhen.GATE_FAIL in verify_repro.skip_when
    assert SkipWhen.GATE_UNCERTAIN in verify_repro.skip_when
    assert {s.key for s in VERIFY_PIPELINE} >= {"audit", "reproduce", "finalize", "report"}
    assert SkipWhen.GATE_FAIL not in by_key["report"].skip_when


def test_resume_compat_extra_fields_on_handoff():
    """断点续跑：完整 output_json 含私有键仍能投影。"""
    h = SourceHandoff.model_validate(
        {"commit_sha": "a", "repo_dirname": "r", "ok": True, "top_level": ["x"]}
    )
    assert h.commit_sha == "a"
    assert not hasattr(h, "ok") or "ok" not in h.model_dump()
