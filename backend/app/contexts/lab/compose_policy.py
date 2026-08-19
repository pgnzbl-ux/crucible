"""AI 生成 Compose 的宿主执行准入策略。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


class ComposePolicyError(ValueError):
    """Compose 包含宿主执行不允许的能力。"""


_SOCKET_NAMES = ("docker.sock", "containerd.sock", "podman.sock")
_SENSITIVE_ROOTS = ("/proc", "/sys", "/dev")
# 任务凭据目录（host_workdir/.secrets，bind 到容器 /workspace/.secrets）：
# 靶场容器是攻击目标，AI 写一行 volumes 就能把明文凭据挂进靶场
_FORBIDDEN_WORKDIR_BINDS = (".secrets",)
_WINDOWS_BIND_RE = re.compile(r"^([A-Za-z]:[\\/][^:]*):")


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path.resolve()), str(root.resolve()))) == str(
            root.resolve()
        )
    except ValueError:
        return False


def _require_local_path(value: str, *, base: Path, workdir: Path, label: str) -> Path:
    if "$" in value or "~" in value:
        raise ComposePolicyError(f"{label} 含无法安全解析的变量或用户目录")
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
    if not _inside(resolved, workdir):
        raise ComposePolicyError(f"{label} 越出 Lab 工作目录")
    return resolved


def _short_volume_source(raw: str) -> str | None:
    match = _WINDOWS_BIND_RE.match(raw)
    if match:
        return match.group(1)
    parts = raw.rsplit(":", 2)
    if len(parts) < 2:
        return None
    return parts[0]


def _is_path_source(source: str) -> bool:
    return (
        source.startswith((".", "/", "\\", "~", "$"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", source))
    )


def _validate_volumes(
    volumes: Any, *, compose_dir: Path, workdir: Path, service_name: str
) -> None:
    if volumes is None:
        return
    if not isinstance(volumes, list):
        raise ComposePolicyError(f"服务 {service_name} volumes 必须是列表")
    for item in volumes:
        if isinstance(item, str):
            lowered = item.lower()
            if any(name in lowered for name in _SOCKET_NAMES):
                raise ComposePolicyError(f"服务 {service_name} 禁止挂载容器运行时 socket")
            source = _short_volume_source(item)
            if source and _is_path_source(source):
                normalized = source.replace("\\", "/").lower()
                if any(
                    normalized == root or normalized.startswith(f"{root}/")
                    for root in _SENSITIVE_ROOTS
                ):
                    raise ComposePolicyError(f"服务 {service_name} 禁止挂载宿主敏感目录")
                resolved = _require_local_path(
                    source,
                    base=compose_dir,
                    workdir=workdir,
                    label=f"服务 {service_name} bind mount",
                )
                _reject_forbidden_workdir_bind(resolved, workdir, service_name)
            continue
        if not isinstance(item, dict):
            raise ComposePolicyError(f"服务 {service_name} volume 格式无效")
        source = str(item.get("source") or "")
        target = str(item.get("target") or "")
        if any(name in f"{source} {target}".lower() for name in _SOCKET_NAMES):
            raise ComposePolicyError(f"服务 {service_name} 禁止挂载容器运行时 socket")
        if item.get("type") == "bind":
            resolved = _require_local_path(
                source,
                base=compose_dir,
                workdir=workdir,
                label=f"服务 {service_name} bind mount",
            )
            _reject_forbidden_workdir_bind(resolved, workdir, service_name)


def _reject_forbidden_workdir_bind(resolved: Path, workdir: Path, service_name: str) -> None:
    """工作区内禁止 bind 到任务凭据等平台敏感子目录。"""
    rel = resolved.relative_to(workdir) if _inside(resolved, workdir) else None
    if rel is None:
        return
    first = rel.parts[0] if rel.parts else ""
    if first.lower() in _FORBIDDEN_WORKDIR_BINDS:
        raise ComposePolicyError(
            f"服务 {service_name} 禁止挂载任务凭据目录 {first}/"
        )


def _validate_build(
    build: Any, *, compose_dir: Path, workdir: Path, service_name: str
) -> None:
    if build is None:
        return
    if isinstance(build, str):
        context = build
        dockerfile = None
    elif isinstance(build, dict):
        context = str(build.get("context") or ".")
        dockerfile = build.get("dockerfile")
    else:
        raise ComposePolicyError(f"服务 {service_name} build 格式无效")
    if "://" in context:
        if not context.startswith("https://"):
            raise ComposePolicyError(f"服务 {service_name} 远程 build context 必须使用 HTTPS")
        if dockerfile:
            raise ComposePolicyError(f"服务 {service_name} 远程 context 禁止自定义 Dockerfile 路径")
        return
    context_path = _require_local_path(
        context,
        base=compose_dir,
        workdir=workdir,
        label=f"服务 {service_name} build context",
    )
    if dockerfile:
        _require_local_path(
            str(dockerfile),
            base=context_path,
            workdir=workdir,
            label=f"服务 {service_name} Dockerfile",
        )


def validate_compose_file(compose_path: str, workdir: str) -> None:
    """解析并拒绝可直接突破宿主边界的 Compose 能力。"""
    path = Path(compose_path).resolve()
    root = Path(workdir).resolve()
    if not _inside(path, root):
        raise ComposePolicyError("Compose 文件越出 Lab 工作目录")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ComposePolicyError(f"Compose 无法安全解析: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise ComposePolicyError("Compose 必须包含 services 对象")
    for name, service in document["services"].items():
        if not isinstance(service, dict):
            raise ComposePolicyError(f"服务 {name} 配置必须是对象")
        if service.get("privileged") is True:
            raise ComposePolicyError(f"服务 {name} 禁止 privileged")
        for field in ("network_mode", "pid", "ipc"):
            if str(service.get(field) or "").lower() == "host":
                raise ComposePolicyError(f"服务 {name} 禁止 {field}: host")
        if service.get("devices"):
            raise ComposePolicyError(f"服务 {name} 禁止 devices")
        if service.get("cap_add"):
            raise ComposePolicyError(f"服务 {name} 禁止 cap_add")
        _validate_volumes(
            service.get("volumes"),
            compose_dir=path.parent,
            workdir=root,
            service_name=str(name),
        )
        _validate_build(
            service.get("build"),
            compose_dir=path.parent,
            workdir=root,
            service_name=str(name),
        )
