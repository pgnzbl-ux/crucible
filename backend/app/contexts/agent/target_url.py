"""靶场地址：探活走回环，对外展示用宿主机可达 IP，容器内复现走 host.docker.internal。"""
from __future__ import annotations

import socket
from urllib.parse import urlparse, urlunparse

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


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
    """把 localhost / 127.0.0.1 换成对外 IP，端口保留。"""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK:
        return url if "://" in url else f"http://{url}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    netloc = f"{advertise_ip}:{port}"
    scheme = parsed.scheme or "http"
    return urlunparse((scheme, netloc, parsed.path or "", parsed.params, parsed.query, parsed.fragment))


def rewrite_url_for_agent_container(url: str | None, advertise_ip: str | None = None) -> str | None:
    """复现容器内把宿主机靶标改成 host.docker.internal。"""
    if not url:
        return url
    rewritten = (
        url.replace("http://localhost", "http://host.docker.internal")
        .replace("https://localhost", "https://host.docker.internal")
        .replace("http://127.0.0.1", "http://host.docker.internal")
        .replace("https://127.0.0.1", "https://host.docker.internal")
    )
    ip = advertise_ip if advertise_ip is not None else host_advertise_ip()
    if ip and ip not in _LOOPBACK:
        rewritten = rewritten.replace(f"http://{ip}", "http://host.docker.internal")
        rewritten = rewritten.replace(f"https://{ip}", "https://host.docker.internal")
    return rewritten
