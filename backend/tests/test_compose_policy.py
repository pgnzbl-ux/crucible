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
        {"build": {"context": "../../host"}},
        {"build": {"context": ".", "dockerfile": "../../Dockerfile"}},
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
                    }
                },
                "volumes": {"app-data": {}},
            }
        ),
        encoding="utf-8",
    )

    validate_compose_file(str(compose), str(tmp_path))
