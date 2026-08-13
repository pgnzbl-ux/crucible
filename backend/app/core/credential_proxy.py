"""
任务级凭据注入（P1-6 Credential Proxy）。

职责：把任务引用的凭据（Credential）注入 agent-runner 容器，凭据零落盘：
- env_var 凭据 → 合并到 docker run --env（容器销毁 env 消失）
- file 凭据 → 写 host_workdir/.secrets/<target>（权限 600），容器内
  /workspace/.secrets/<target>，任务结束随 host_workdir rmtree 销毁

返回 secret_files 描述列表，供 prompt 告知 agent 有哪些凭据可用（env 名 / 文件路径）。

安全：
- file 凭据目录 0o700、文件 0o600（Linux host 生效；Windows host 无 Unix 权限，容器内
  受 user=1000 约束，权限位 best-effort）
- 凭据值从不进镜像层 / 不落 git，只在运行时注入
"""

from __future__ import annotations

import os
from typing import Any

from app.contexts.settings.models import Credential

# host_workdir 下的密钥子目录（bind mount → 容器内 /workspace/.secrets）
SECRET_DIR_NAME = ".secrets"
SECRET_DIR_CONTAINER = "/workspace/.secrets"


def inject_credentials(
    credentials: list[Credential],
    runner_env: dict[str, str],
    host_workdir: str,
) -> list[dict[str, Any]]:
    """注入凭据到 runner env + host_workdir/.secrets/。

    - env_var：原地合并进 runner_env（target 作为环境变量名）
    - file：写 host_workdir/.secrets/<target>（600）

    返回 secret_files 描述列表（注入 prompt 告知 agent）：
        [{"kind": "env_var", "target": "DB_PASSWORD", "description": "..."},
         {"kind": "file", "target": "tls.key", "path": "/workspace/.secrets/tls.key", "description": "..."}]
    """
    secret_files: list[dict[str, Any]] = []
    if not credentials:
        return secret_files

    secret_dir = os.path.join(host_workdir, SECRET_DIR_NAME)

    for cred in credentials:
        plain = cred.secret_encrypted
        if not plain:
            # 空值跳过,不阻断任务
            continue

        if cred.kind == "env_var":
            runner_env[cred.target] = plain
            secret_files.append({
                "kind": "env_var",
                "target": cred.target,
                "description": cred.description,
            })
        elif cred.kind == "file":
            os.makedirs(secret_dir, mode=0o700, exist_ok=True)
            fpath = os.path.join(secret_dir, cred.target)
            # O_CREAT + 0o600，避免 umask 导致权限过宽
            fd = os.open(fpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, plain.encode("utf-8"))
            finally:
                os.close(fd)
            os.chmod(fpath, 0o600)
            secret_files.append({
                "kind": "file",
                "target": cred.target,
                "path": f"{SECRET_DIR_CONTAINER}/{cred.target}",
                "description": cred.description,
            })

    return secret_files
