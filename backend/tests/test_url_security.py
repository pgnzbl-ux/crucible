"""LLM Base URL 必须是解析到公网地址的 HTTPS 域名。"""
import socket
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "addresses", "message"),
    [
        ("http://api.example.com", ["93.184.216.34"], "HTTPS"),
        ("https://127.0.0.1", [], "域名"),
        ("https://user:pass@api.example.com", ["93.184.216.34"], "userinfo"),
        ("https://api.example.com/path#fragment", ["93.184.216.34"], "fragment"),
        ("https://api.example.com", ["127.0.0.1"], "公网"),
        ("https://api.example.com", ["10.0.0.8"], "公网"),
        ("https://api.example.com", ["169.254.169.254"], "公网"),
        ("https://api.example.com", ["100.64.0.1"], "公网"),
    ],
)
async def test_validate_public_https_url_rejects_unsafe_targets(url, addresses, message):
    from app.core.url_security import validate_public_https_url

    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))
        for address in addresses
    ]
    with patch("app.core.url_security.socket.getaddrinfo", return_value=infos):
        with pytest.raises(ValueError, match=message):
            await validate_public_https_url(url)


@pytest.mark.asyncio
async def test_validate_public_https_url_accepts_unknown_public_domain():
    from app.core.url_security import validate_public_https_url

    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
        (
            socket.AF_INET6,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0),
        ),
    ]
    with patch("app.core.url_security.socket.getaddrinfo", return_value=infos):
        assert await validate_public_https_url("https://custom-llm.example/v1") == "https://custom-llm.example/v1"


@pytest.mark.asyncio
async def test_validate_public_https_url_accepts_tun_fake_ip():
    """Clash/Surge TUN fake-ip（198.18.0.0/15）不是真实内网，放行以免误杀本地代理。"""
    from app.core.url_security import validate_public_https_url

    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("198.18.0.12", 443)),
    ]
    with patch("app.core.url_security.socket.getaddrinfo", return_value=infos):
        assert (
            await validate_public_https_url("https://api.deepseek.com/anthropic")
            == "https://api.deepseek.com/anthropic"
        )


@pytest.mark.asyncio
async def test_validate_public_https_url_rejects_unresolvable_domain():
    from app.core.url_security import validate_public_https_url

    with patch(
        "app.core.url_security.socket.getaddrinfo",
        side_effect=socket.gaierror("not found"),
    ):
        with pytest.raises(ValueError, match="无法解析"):
            await validate_public_https_url("https://missing.example")
