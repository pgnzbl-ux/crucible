"""外部服务 URL 安全校验。"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

# Clash/Surge 等 TUN fake-ip 常用 RFC2544 基准测试网段；不路由到真实内网主机。
_ALLOWED_NON_GLOBAL = (ipaddress.ip_network("198.18.0.0/15"),)


def _is_allowed_resolved_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.is_global:
        return True
    return any(ip in network for network in _ALLOWED_NON_GLOBAL)


def normalize_https_domain_url(value: str) -> str:
    """仅接受无 userinfo/fragment/IP 字面量的 HTTPS 域名 URL。"""
    raw = value.strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base_url 格式或端口无效") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError("base_url 必须使用 HTTPS")
    if not parsed.hostname:
        raise ValueError("base_url 必须包含域名")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url 禁止包含 userinfo")
    if parsed.fragment:
        raise ValueError("base_url 禁止包含 fragment")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ValueError("base_url 必须使用域名，禁止 IP 字面量")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("base_url 端口无效")
    return raw


async def validate_public_https_url(value: str) -> str:
    """校验 URL 域名当前所有 DNS 结果均为公网地址。"""
    normalized = normalize_https_domain_url(value)
    parsed = urlsplit(normalized)
    port = parsed.port or 443
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("base_url 域名无法解析") from exc
    addresses = {info[4][0].split("%", 1)[0] for info in infos if info[4]}
    if not addresses:
        raise ValueError("base_url 域名无法解析")
    for address in addresses:
        try:
            allowed = _is_allowed_resolved_ip(address)
        except ValueError as exc:
            raise ValueError("base_url DNS 返回了无效地址") from exc
        if not allowed:
            raise ValueError("base_url 域名必须仅解析到公网地址")
    return normalized
