"""节点失败分类与打包。"""
import os
import sys
import tarfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.contexts.agent.node_failure import (
    classify_node_error,
    pack_node_run_bundle,
    snapshot_attempt,
)


@pytest.mark.parametrize(
    ("stage", "text", "expected"),
    [
        ("recipe_validation", "compose 未映射", "recipe_validation"),
        ("port_conflict", "端口占用", "port_conflict"),
        ("compose_up", "COPY Eureka-Server /app", "compose_up.copy"),
        ("compose_up", "Could not transfer artifact log4j", "compose_up.transfer"),
        ("compose_up", "failed to build: maven", "compose_up.build"),
        ("compose_up", "denied by network policy", "compose_up.policy"),
        ("compose_up", "container exited 1", "compose_up.runtime"),
        ("health_check", "健康检查不过", "health_check"),
        (None, "未产出 .node_output.json", "runner.no_submit"),
        (None, "Timeout after 600s", "runner.timeout"),
        (None, "Cannot connect to Docker daemon", "docker.unavailable"),
        (None, "", "unknown"),
    ],
)
def test_classify_node_error_table(stage, text, expected):
    assert classify_node_error(failed_stage=stage, error_text=text) == expected


def test_two_attempts_keep_first_vuln_env(tmp_path):
    work = tmp_path / "audit"
    env = work / ".vuln-env"
    env.mkdir(parents=True)
    (env / "docker-compose.yml").write_text("round: 1\n", encoding="utf-8")
    snapshot_attempt(
        str(work), "env_ready", 1,
        previous_error="e1",
        platform_error="p1",
        copy_vuln_env=True,
    )
    (env / "docker-compose.yml").write_text("round: 2\n", encoding="utf-8")
    snapshot_attempt(
        str(work), "env_ready", 2,
        previous_error="e2",
        platform_error="p2",
        copy_vuln_env=True,
    )
    a1 = work / ".node-failure" / "env_ready" / "attempts" / "1" / ".vuln-env" / "docker-compose.yml"
    a2 = work / ".node-failure" / "env_ready" / "attempts" / "2" / ".vuln-env" / "docker-compose.yml"
    assert a1.read_text(encoding="utf-8") == "round: 1\n"
    assert a2.read_text(encoding="utf-8") == "round: 2\n"

    bundle = pack_node_run_bundle(
        str(work),
        "env_ready",
        {"node_key": "env_ready", "error_class": "health_check", "attempt_count": 2},
    )
    names = tarfile.open(fileobj=BytesIO(bundle), mode="r:gz").getnames()
    assert "manifest.json" in names
    assert any(n.endswith("attempts/1/.vuln-env/docker-compose.yml") for n in names)
    assert any(n.endswith("attempts/2/.vuln-env/docker-compose.yml") for n in names)


def test_pack_skips_secrets_and_redacts_tokens(tmp_path):
    work = tmp_path / "audit"
    env = work / ".vuln-env"
    env.mkdir(parents=True)
    (env / "ok.yml").write_text("ok\n", encoding="utf-8")
    secrets = work / ".secrets"
    secrets.mkdir()
    (secrets / "token").write_text("sk-abcdefghijklmnopqrstuvwxyz", encoding="utf-8")
    (env / ".secrets").mkdir()
    (env / ".secrets" / "key").write_text("secret", encoding="utf-8")
    (work / ".claude").mkdir()
    (work / ".claude" / "session.jsonl").write_text(
        '{"env":"ANTHROPIC_API_KEY=sk-abcdefghijklmnopqrstuvwxyz"}\n',
        encoding="utf-8",
    )
    snapshot_attempt(
        str(work), "audit", 1,
        platform_error="fail ANTHROPIC_API_KEY=sk-abcdefghijklmnopqrstuvwxyz",
        copy_vuln_env=True,
    )
    bundle = pack_node_run_bundle(str(work), "audit", {"node_key": "audit"})
    tar = tarfile.open(fileobj=BytesIO(bundle), mode="r:gz")
    names = tar.getnames()
    assert not any(".secrets" in n for n in names)
    session = tar.extractfile("attempts/1/session.jsonl").read().decode("utf-8")
    assert "sk-" not in session
    assert "[REDACTED]" in session
