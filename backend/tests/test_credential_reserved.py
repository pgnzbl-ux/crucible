"""任务凭据不得覆盖 runner 保留环境变量。"""
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


def _cred(target: str, kind: str = "env_var") -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        target=target,
        secret_encrypted="stolen",
        description="d",
    )


@pytest.mark.parametrize(
    "target",
    [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "HOME",
        "PATH",
        "BASH_ENV",
        "NODE_KEY",
    ],
)
def test_create_request_rejects_reserved_env(target):
    from app.contexts.settings.schemas import CredentialCreateRequest

    with pytest.raises(ValidationError):
        CredentialCreateRequest(name="x", kind="env_var", target=target, secret="s")


def test_create_request_allows_normal_env():
    from app.contexts.settings.schemas import CredentialCreateRequest

    req = CredentialCreateRequest(name="db", kind="env_var", target="DB_PASSWORD", secret="s")
    assert req.target == "DB_PASSWORD"


def test_inject_skips_reserved_env(tmp_path):
    from app.core.credential_proxy import inject_credentials

    env = {"ANTHROPIC_API_KEY": "platform-key", "HOME": "/tmp", "PATH": "/usr/bin"}
    inject_credentials(
        [_cred("ANTHROPIC_API_KEY"), _cred("HOME"), _cred("DB_PASSWORD")],
        env,
        str(tmp_path),
    )
    assert env["ANTHROPIC_API_KEY"] == "platform-key"
    assert env["HOME"] == "/tmp"
    assert env["DB_PASSWORD"] == "stolen"


def test_inject_skips_reserved_even_if_already_in_db(tmp_path):
    from app.core.credential_proxy import inject_credentials

    env = {"NODE_KEY": "from-platform"}
    files = inject_credentials([_cred("NODE_KEY")], env, str(tmp_path))
    assert env["NODE_KEY"] == "from-platform"
    assert files == []
