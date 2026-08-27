"""不可信 Compose 在宿主执行前必须拒绝明确高危能力。"""
import pytest


@pytest.mark.parametrize(
    "service",
    [
        {"image": "demo", "privileged": True},
        {"image": "demo", "network_mode": "host"},
        {"image": "demo", "pid": "host"},
        {"image": "demo", "ipc": "host"},
        {"image": "demo", "devices": ["/dev/kvm:/dev/kvm"]},
        {"image": "demo", "cap_add": ["SYS_ADMIN"]},
        {"image": "demo", "volumes": ["/var/run/docker.sock:/var/run/docker.sock"]},
        {"image": "demo", "volumes": ["../../host:/data"]},
        # 任务凭据目录：workdir 内合法路径，但靶场容器禁止挂载（AI 一行 volumes 即泄密）
        {"image": "demo", "volumes": ["../../../.secrets/tls.key:/tls.key"]},
        {"image": "demo", "volumes": ["/abs/.secrets/x:/x"]},
        {"build": {"context": "../../host"}},
        {"build": {"context": ".", "dockerfile": "../../Dockerfile"}},
        {"image": "demo", "env_file": [".env"]},
        {"image": "demo", "security_opt": ["seccomp:unconfined"]},
        {"image": "demo", "user": "0"},
        {"image": "demo", "userns_mode": "host"},
        {
            "image": "demo",
            "deploy": {"resources": {"reservations": {"devices": [{"capabilities": ["gpu"]}]}}},
        },
        {"image": "demo", "environment": ["LEAK=${AUTH_SECRET}"]},
        {"image": "demo", "secrets": ["app_secret"]},
        {"image": "demo", "configs": ["app_config"]},
        {"image": "demo", "network_mode": "container:other_container"},
        {"image": "demo", "pid": "container:other_container"},
        {"image": "demo", "ipc": "container:other_container"},
        {"image": "demo", "network_mode": "service:other_service"},
    ],
)
def test_compose_policy_rejects_dangerous_services(tmp_path, service):
    import yaml

    from app.contexts.lab.compose_policy import ComposePolicyError, validate_compose_file

    recipe = tmp_path / ".vuln-env"
    recipe.mkdir()
    compose = recipe / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump({"services": {"app": service}}),
        encoding="utf-8",
    )

    with pytest.raises(ComposePolicyError):
        validate_compose_file(str(compose), str(tmp_path))


@pytest.mark.parametrize(
    "document",
    [
        {
            "services": {"app": {"image": "demo"}},
            "networks": {"infra": {"external": True}},
        },
        {
            "services": {"app": {"image": "demo"}},
            "volumes": {"data": {"driver_opts": {"type": "nfs", "device": ":/export"}}},
        },
        {
            "include": [{"path": "other.yml"}],
            "services": {"app": {"image": "demo"}},
        },
        {
            "secrets": {"k": {"file": "/etc/shadow"}},
            "services": {"app": {"image": "demo"}},
        },
        {
            "configs": {"k": {"file": "/etc/passwd"}},
            "services": {"app": {"image": "demo"}},
        },
    ],
)
def test_compose_policy_rejects_dangerous_top_level(tmp_path, document):
    import yaml

    from app.contexts.lab.compose_policy import ComposePolicyError, validate_compose_file

    recipe = tmp_path / ".vuln-env"
    recipe.mkdir()
    compose = recipe / "docker-compose.yml"
    compose.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ComposePolicyError):
        validate_compose_file(str(compose), str(tmp_path))


def test_compose_policy_allows_normal_project_recipe(tmp_path):
    import yaml

    from app.contexts.lab.compose_policy import validate_compose_file

    recipe = tmp_path / ".vuln-env"
    context = recipe / "app"
    context.mkdir(parents=True)
    (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    compose = recipe / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "app": {
                        "build": {"context": "./app", "dockerfile": "Dockerfile"},
                        "ports": ["3001:3000"],
                        "volumes": ["app-data:/data", "./app:/src:ro"],
                        "environment": ["FOO=bar", "PATH=/usr/bin"],
                    }
                },
                "volumes": {"app-data": {}},
            }
        ),
        encoding="utf-8",
    )

    validate_compose_file(str(compose), str(tmp_path))


def test_compose_subprocess_env_excludes_platform_secrets(monkeypatch):
    from app.contexts.lab.compose_policy import compose_subprocess_env

    monkeypatch.setenv("AUTH_SECRET", "super-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("PATH", "/custom/bin")
    monkeypatch.setenv("HOME", "/home/crucible")

    env = compose_subprocess_env()
    assert "AUTH_SECRET" not in env
    assert "DATABASE_URL" not in env
    assert env["PATH"] == "/custom/bin"
    assert env["HOME"] == "/home/crucible"
    assert env.get("BUILDKIT_PROGRESS") == "plain"


@pytest.mark.asyncio
async def test_compose_up_build_runs_policy_before_docker(tmp_path, monkeypatch):
    """rebuild 路径必须过策略：privileged 不得触达 docker CLI。"""
    import yaml

    from app.contexts.lab import docker_ops
    from app.contexts.lab.compose_policy import ComposePolicyError

    recipe = tmp_path / ".vuln-env"
    recipe.mkdir()
    compose = recipe / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {"services": {"app": {"image": "demo", "privileged": True}}}
        ),
        encoding="utf-8",
    )

    called = {"run": False}

    async def _boom(*_a, **_k):
        called["run"] = True
        raise AssertionError("策略拒绝后不应执行 docker")

    monkeypatch.setattr(docker_ops, "_run", _boom)

    with pytest.raises(ComposePolicyError, match="privileged"):
        await docker_ops.compose_up_build("crucible-lab-x", str(compose), str(tmp_path))
    assert called["run"] is False


@pytest.mark.asyncio
async def test_compose_up_build_passes_whitelist_env(tmp_path, monkeypatch):
    import yaml

    from app.contexts.lab import docker_ops

    recipe = tmp_path / ".vuln-env"
    recipe.mkdir()
    compose = recipe / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump({"services": {"app": {"image": "demo"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTH_SECRET", "should-not-leak")

    captured: dict = {}

    async def _capture(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(docker_ops, "_run", _capture)
    await docker_ops.compose_up_build("crucible-lab-x", str(compose), str(tmp_path))
    assert captured["env"] is not None
    assert "AUTH_SECRET" not in captured["env"]
    assert "PATH" in captured["env"]
