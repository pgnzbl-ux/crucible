"""Compose / docker 端口解析与改写。"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_SIDECAR_CONTAINER_PORTS = {3306, 5432, 6379, 27017, 5672, 1433, 9200, 11211}
_SHORT_PORT = re.compile(
    r"^(?:(?:\d{1,3}\.){3}\d{1,3}:)?(\d+):(\d+)(?:/(?:tcp|udp))?$", re.I
)
_BARE_PORT = re.compile(r"^(\d+)(?:/(?:tcp|udp))?$")


def parse_compose_port_mappings(text: str) -> list[tuple[int, int]]:
    """从 compose 文本抽出 (宿主机端口, 容器端口)。"""
    mappings: list[tuple[int, int]] = []
    in_ports = False
    ports_indent = 0
    pending_target: int | None = None
    pending_published: int | None = None

    def flush_long() -> None:
        nonlocal pending_target, pending_published
        if pending_published is not None and pending_target is not None:
            mappings.append((pending_published, pending_target))
        elif pending_target is not None and pending_published is None:
            mappings.append((pending_target, pending_target))
        pending_target = None
        pending_published = None

    for raw in (text or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if stripped.startswith("ports:"):
            flush_long()
            in_ports = True
            ports_indent = indent
            continue
        if in_ports and indent <= ports_indent and not stripped.startswith("-"):
            flush_long()
            in_ports = False
        if not in_ports:
            continue
        target_m = re.match(r"target:\s*(\d+)\s*$", stripped)
        if target_m:
            pending_target = int(target_m.group(1))
            continue
        published_m = re.match(r"published:\s*[\"']?(\d+)[\"']?\s*$", stripped)
        if published_m:
            pending_published = int(published_m.group(1))
            continue
        if stripped.startswith("-"):
            flush_long()
            rest = stripped[1:].strip().strip("\"'")
            target_inline = re.match(r"target:\s*(\d+)\s*$", rest)
            if target_inline:
                pending_target = int(target_inline.group(1))
                continue
            published_inline = re.match(r"published:\s*[\"']?(\d+)[\"']?\s*$", rest)
            if published_inline:
                pending_published = int(published_inline.group(1))
                continue
            short = _SHORT_PORT.match(rest)
            if short:
                mappings.append((int(short.group(1)), int(short.group(2))))
                continue
            bare = _BARE_PORT.match(rest)
            if bare:
                port = int(bare.group(1))
                mappings.append((port, port))
    flush_long()
    return mappings


def web_host_ports(mappings: list[tuple[int, int]]) -> list[int]:
    """只保留映射到宿主机的 Web 入口；数据库/MQ 端口不算靶场地址。"""
    seen: set[int] = set()
    ports: list[int] = []
    for host_port, container_port in mappings:
        if container_port in _SIDECAR_CONTAINER_PORTS:
            continue
        if host_port in seen:
            continue
        seen.add(host_port)
        ports.append(host_port)
    return ports


def web_container_ports(mappings: list[tuple[int, int]]) -> list[int]:
    """与 web_host_ports 同序的容器侧端口（供 scheme 推断）。"""
    seen: set[int] = set()
    ports: list[int] = []
    for host_port, container_port in mappings:
        if container_port in _SIDECAR_CONTAINER_PORTS:
            continue
        if host_port in seen:
            continue
        seen.add(host_port)
        ports.append(container_port)
    return ports


_SHORT_HOST_IN_LINE = re.compile(
    r"(?:(?:\d{1,3}\.){3}\d{1,3}:)?(?P<host>\d+):\d+(?:/(?:tcp|udp))?",
    re.I,
)
_PUBLISHED_HOST_IN_LINE = re.compile(
    r"(published:\s*[\"']?)(\d+)",
    re.I,
)


def _pick_free_host_port(start: int, taken: set[int]) -> int | None:
    candidate = start
    while candidate in taken:
        candidate += 1
        if candidate > 65535:
            return None
    return candidate


def _apply_host_port_replacements(text: str, replacements: dict[int, int]) -> str:
    """只改 ports 段里短语法 HOST:CONTAINER 的宿主侧，以及 published: 行。"""
    lines: list[str] = []
    in_ports = False
    ports_indent = 0
    for raw in (text or "").splitlines(keepends=True):
        body = raw[:-2] if raw.endswith("\r\n") else (raw[:-1] if raw.endswith("\n") else raw)
        nl = raw[len(body):]
        stripped = body.strip()
        indent = len(body) - len(body.lstrip(" "))
        if stripped.startswith("ports:"):
            in_ports = True
            ports_indent = indent
            lines.append(raw)
            continue
        if in_ports and stripped and indent <= ports_indent and not stripped.startswith("-"):
            in_ports = False
        if not in_ports or not stripped or stripped.startswith("#"):
            lines.append(raw)
            continue
        if "published:" in stripped:
            pub = _PUBLISHED_HOST_IN_LINE.search(body)
            if pub:
                port = int(pub.group(2))
                if port in replacements:
                    body = (
                        body[: pub.start(2)]
                        + str(replacements[port])
                        + body[pub.end(2) :]
                    )
                    lines.append(body + nl)
                    continue
        else:
            short = _SHORT_HOST_IN_LINE.search(body)
            if short:
                host = int(short.group("host"))
                if host in replacements:
                    body = (
                        body[: short.start("host")]
                        + str(replacements[host])
                        + body[short.end("host") :]
                    )
                    lines.append(body + nl)
                    continue
        lines.append(raw)
    return "".join(lines)


def rewrite_compose_host_ports(text: str, occupied: set[int]) -> str | None:
    """冲突的 Web 宿主口改为空闲口；只改 host 侧。无 Web 映射返回 None。"""
    mappings = parse_compose_port_mappings(text)
    web_ports = web_host_ports(mappings)
    if not web_ports:
        return None
    occupied_set = set(occupied)
    conflicts = [p for p in web_ports if p in occupied_set]
    if not conflicts:
        return text

    taken = set(occupied_set)
    taken.update(p for p in web_ports if p not in occupied_set)
    replacements: dict[int, int] = {}
    for host in conflicts:
        picked = _pick_free_host_port(host + 1, taken)
        if picked is None:
            return None
        replacements[host] = picked
        taken.add(picked)
    return _apply_host_port_replacements(text, replacements)


def load_web_host_ports(compose_abs: str) -> list[int]:
    from pathlib import Path

    try:
        text = Path(compose_abs).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return web_host_ports(parse_compose_port_mappings(text))


def load_web_container_ports(compose_abs: str) -> list[int]:
    """与 load_web_host_ports 同序的容器侧端口（host_port 去重后对位）。

    用于探活推断入口 scheme（443/8443 → https）。容器侧信息在去重时可能
    丢失（同一 host 口多个 target），此时对位退化为 None → http，可接受。
    """
    from pathlib import Path

    try:
        text = Path(compose_abs).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return web_container_ports(parse_compose_port_mappings(text))


_PUBLISHED_HOST_PORT = re.compile(r"(\d+)->")


def parse_docker_ps_published_ports(
    text: str, *, exclude_project: str | None = None
) -> set[int]:
    """从 `docker ps --format '{{.Label project}}\\t{{.Ports}}'` 抽出宿主已映射端口。"""
    occupied: set[int] = set()
    skip = (exclude_project or "").strip().lower()
    for raw in (text or "").splitlines():
        project, sep, ports = raw.partition("\t")
        if not sep:
            ports = raw
            project = ""
        if skip and project.strip().lower() == skip:
            continue
        for match in _PUBLISHED_HOST_PORT.finditer(ports):
            occupied.add(int(match.group(1)))
    return occupied


def list_docker_occupied_host_ports(*, exclude_project: str | None = None) -> set[int]:
    """查当前运行中容器已 publish 到宿主的端口。查失败则当无占用，交给 compose up 暴露。"""
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--format",
                '{{.Label "com.docker.compose.project"}}\t{{.Ports}}',
            ],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:  # noqa: BLE001
        logger.warning("docker ps 查占用端口失败", exc_info=True)
        return set()
    if result.returncode != 0:
        logger.warning(
            "docker ps 查占用端口失败: %s", (result.stderr or result.stdout)[:300]
        )
        return set()
    return parse_docker_ps_published_ports(result.stdout, exclude_project=exclude_project)
