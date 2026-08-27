"""Compose / docker 端口解析与改写。"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SIDECAR_CONTAINER_PORTS = {
    # DB
    3306,
    5432,
    1433,
    27017,
    # cache
    6379,
    11211,
    # MQ
    5672,
    9092,
    4222,
    # search
    9200,
    9300,
    # object storage API / console（会答 HTTP，但不是复现 Web 入口）
    9000,
    9001,
}
_SHORT_PORT = re.compile(
    r"^(?:(?:\d{1,3}\.){3}\d{1,3}:)?(\d+):(\d+)(?:/(tcp|udp))?$", re.I
)


def parse_compose_port_mappings(text: str) -> list[tuple[int, int]]:
    """从 compose 文本抽出 (宿主机端口, 容器端口)。"""
    mappings: list[tuple[int, int]] = []
    in_ports = False
    ports_indent = 0
    pending_target: int | None = None
    pending_published: int | None = None
    pending_protocol = "tcp"

    def flush_long() -> None:
        nonlocal pending_target, pending_published, pending_protocol
        if (
            pending_protocol == "tcp"
            and pending_published is not None
            and pending_target is not None
        ):
            mappings.append((pending_published, pending_target))
        pending_target = None
        pending_published = None
        pending_protocol = "tcp"

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
        protocol_m = re.match(r"protocol:\s*[\"']?(tcp|udp)[\"']?\s*$", stripped, re.I)
        if protocol_m:
            pending_protocol = protocol_m.group(1).lower()
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
                if (short.group(3) or "tcp").lower() != "tcp":
                    continue
                mappings.append((int(short.group(1)), int(short.group(2))))
                continue
    flush_long()
    return mappings


def _container_port_from_decl(item: Any) -> tuple[int | None, str]:
    """返回声明中的 (container_port, protocol)，宿主端口可由 Docker 动态分配。"""
    if isinstance(item, int):
        return item, "tcp"
    if isinstance(item, str):
        raw = item.strip().strip("\"'")
        base, slash, protocol = raw.rpartition("/")
        if slash and protocol.lower() in {"tcp", "udp"}:
            raw = base
        else:
            protocol = "tcp"
        container = raw.rsplit(":", 1)[-1]
        if "-" in container:
            container = container.split("-", 1)[0]
        return (int(container), protocol.lower()) if container.isdigit() else (None, protocol.lower())
    if isinstance(item, dict):
        target = item.get("target")
        text = str(target or "")
        protocol = str(item.get("protocol") or "tcp").lower()
        return (int(text), protocol) if text.isdigit() else (None, protocol)
    return None, "tcp"


def compose_declares_web_port(text: str) -> bool:
    """Compose 是否声明了至少一个 TCP Web 候选端口。

    这里只做 up 前准入；真实 host_ip/host_port 必须在 up 后读 Docker inspect。
    """
    try:
        document = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return False
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        return False
    for service in services.values():
        if not isinstance(service, dict):
            continue
        declarations = service.get("ports")
        if not isinstance(declarations, list):
            continue
        for item in declarations:
            container_port, protocol = _container_port_from_decl(item)
            if (
                container_port is not None
                and protocol == "tcp"
                and container_port not in _SIDECAR_CONTAINER_PORTS
            ):
                return True
    return False


def load_compose_declares_web_port(compose_abs: str) -> bool:
    try:
        text = Path(compose_abs).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return compose_declares_web_port(text)


def web_runtime_bindings(
    bindings: list[dict[str, Any]],
) -> list[dict[str, str | int]]:
    """过滤 Docker inspect 的实际 TCP Web 绑定，并折叠双栈重复项。"""
    selected: dict[tuple[int, int], dict[str, str | int]] = {}
    for item in bindings:
        try:
            host_port = int(item.get("host_port"))
            container_port = int(item.get("container_port"))
        except (TypeError, ValueError):
            continue
        if str(item.get("protocol") or "tcp").lower() != "tcp":
            continue
        if container_port in _SIDECAR_CONTAINER_PORTS:
            continue
        normalized = {
            "host_ip": str(item.get("host_ip") or "0.0.0.0"),
            "host_port": host_port,
            "container_port": container_port,
            "protocol": "tcp",
        }
        key = (host_port, container_port)
        current = selected.get(key)
        # Docker 双栈通常同时返回 0.0.0.0 与 ::；优先 IPv4，匹配现有 target_url 契约。
        if current is None or str(current["host_ip"]) in {"::", "[::]"}:
            selected[key] = normalized
    return list(selected.values())


def probe_host_for_binding(host_ip: str) -> str | None:
    """把 wildcard 转成本机探测地址；IPv6-only 暂不发布为 IPv4 target_url。"""
    raw = (host_ip or "0.0.0.0").strip().strip("[]")
    if raw in {"", "0.0.0.0"}:
        return "127.0.0.1"
    if raw == "::":
        return None
    return raw


def public_host_for_binding(host_ip: str, advertise_ip: str) -> str | None:
    """返回复现容器可达的发布地址；loopback-only 映射不可对外发布。"""
    raw = (host_ip or "0.0.0.0").strip().strip("[]")
    if raw in {"", "0.0.0.0"}:
        return advertise_ip
    if raw in {"127.0.0.1", "::1", "localhost", "::"}:
        return None
    return raw


async def load_runtime_web_bindings(
    compose_project: str,
) -> list[dict[str, str | int]]:
    from app.contexts.lab.docker_ops import list_published_ports

    return web_runtime_bindings(await list_published_ports(compose_project))


def publishable_runtime_bindings(
    bindings: list[dict[str, str | int]],
    advertise_ip: str,
) -> list[dict[str, str | int]]:
    """补出 probe_host/public_host，只保留宿主与复现容器都可达的绑定。"""
    result: list[dict[str, str | int]] = []
    for item in bindings:
        host_ip = str(item.get("host_ip") or "0.0.0.0")
        probe_host = probe_host_for_binding(host_ip)
        public_host = public_host_for_binding(host_ip, advertise_ip)
        if not probe_host or not public_host:
            continue
        result.append({**item, "probe_host": probe_host, "public_host": public_host})
    return result


def recipe_declared_port(target_url: str | None) -> int | None:
    """从配方 target_url 抽出声明端口（宿主或容器侧，由调用方匹配）。"""
    raw = (target_url or "").strip()
    if not raw:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    if parsed.port is not None:
        return int(parsed.port)
    scheme = (parsed.scheme or "http").lower()
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def filter_bindings_for_recipe(
    bindings: list[dict[str, str | int]],
    *,
    target_url: str | None,
) -> tuple[list[dict[str, str | int]], str | None]:
    """按配方声明入口收窄探活候选。

    - 未声明端口：返回全部 Web 绑定（已排除 sidecar）
    - 声明端口且能匹配 host/container port：只返回匹配项
    - 声明了但发布列表里没有：返回空列表 + 错误说明（禁止降级到旁路 HTTP 口）
    """
    preferred = recipe_declared_port(target_url)
    if preferred is None:
        return list(bindings), None
    matched = [
        item
        for item in bindings
        if int(item.get("host_port") or -1) == preferred
        or int(item.get("container_port") or -1) == preferred
    ]
    if matched:
        return matched, None
    published = sorted(
        {
            f"{int(item['host_port'])}->{int(item['container_port'])}"
            for item in bindings
        }
    )
    return [], (
        f"配方声明入口端口 {preferred} 未出现在已发布 Web 绑定中"
        f"（published={published or '[]'}）。"
        "禁止用其它碰巧通的 HTTP 口冒充靶场地址。"
    )


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
    if not web_ports and not compose_declares_web_port(text):
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
