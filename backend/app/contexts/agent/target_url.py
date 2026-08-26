"""靶场地址：探活走回环，对外与复现一律用宿主机可达 IP:port。

禁止写入 host.docker.internal。回环地址改写成 advertise IP。
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse, urlunparse

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_FORBIDDEN_DOCKER_INTERNAL = "host.docker.internal"


def host_advertise_ip() -> str:
    """探测本机对外网卡 IPv4。失败时才退回 127.0.0.1。"""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        if sock is not None:
            sock.close()
    if ip and ip not in _LOOPBACK and not ip.startswith("169.254."):
        return ip
    try:
        hostname_ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        hostname_ip = ""
    if hostname_ip and hostname_ip not in _LOOPBACK:
        return hostname_ip
    return "127.0.0.1"


def port_from_url(url: str | None) -> int | None:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"http://{url}")
    if parsed.port:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return None


def publish_target_url(port: int, advertise_ip: str | None = None, scheme: str = "http") -> str:
    ip = advertise_ip or host_advertise_ip()
    return f"{scheme}://{ip}:{int(port)}"


def rewrite_loopback_host(url: str, advertise_ip: str) -> str:
    """把 localhost / 127.0.0.1 / ::1 换成对外 IP，端口保留。"""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK:
        return url if "://" in url else f"http://{url}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    netloc = f"{advertise_ip}:{port}"
    scheme = parsed.scheme or "http"
    return urlunparse((scheme, netloc, parsed.path or "", parsed.params, parsed.query, parsed.fragment))


def rewrite_url_for_agent_container(url: str | None, advertise_ip: str | None = None) -> str | None:
    """复现注入：回环与遗留 host.docker.internal 一律改成 advertise IP:port。"""
    if not url:
        return url
    ip = advertise_ip if advertise_ip is not None else host_advertise_ip()
    raw = url if "://" in url else f"http://{url}"
    if _FORBIDDEN_DOCKER_INTERNAL in raw:
        raw = raw.replace(_FORBIDDEN_DOCKER_INTERNAL, ip)
    return rewrite_loopback_host(raw, ip)
