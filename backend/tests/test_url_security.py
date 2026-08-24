"""LLM Base URL：RELAXED=true 不限制；false = 仅 HTTPS 公网域名。"""
import socket
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def strict_llm_url(monkeypatch):
    settings = MagicMock(llm_base_url_relaxed=False)
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)


@pytest.fixture
def relaxed_llm_url(monkeypatch):
    settings = MagicMock(llm_base_url_relaxed=True)
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "addresses", "message"),
    [
        ("http://api.example.com", ["93.184.216.34"], "HTTPS"),
        ("ftp://api.example.com", ["93.184.216.34"], "HTTPS"),
        ("https://user:pass@api.example.com", ["93.184.216.34"], "userinfo"),
        ("https://api.example.com/path#fragment", ["93.184.216.34"], "fragment"),
        ("https://api.example.com", ["127.0.0.1"], "公网"),
        ("https://api.example.com", ["10.0.0.8"], "公网"),
        ("https://127.0.0.1", [], "域名"),
    ],
)
async def test_strict_mode_rejects_unsafe_targets(url, addresses, message, strict_llm_url):
    from app.core.url_security import validate_public_https_url

    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))
        for address in addresses
    ]
    with patch("app.core.url_security.socket.getaddrinfo", return_value=infos):
        with pytest.raises(ValueError, match=message):
            await validate_public_https_url(url)


@pytest.mark.asyncio
async def test_strict_mode_accepts_public_https(strict_llm_url):
    from app.core.url_security import validate_public_https_url

    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
    ]
    with patch("app.core.url_security.socket.getaddrinfo", return_value=infos):
        assert await validate_public_https_url("https://custom-llm.example/v1") == "https://custom-llm.example/v1"


@pytest.mark.asyncio
async def test_relaxed_mode_allows_http_ip_and_loopback(relaxed_llm_url):
    from app.core.url_security import validate_public_https_url

    assert await validate_public_https_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert await validate_public_https_url("http://10.0.0.8:8080/v1") == "http://10.0.0.8:8080/v1"
    assert await validate_public_https_url("https://localhost:8443") == "https://localhost:8443"


@pytest.mark.asyncio
async def test_relaxed_mode_still_rejects_bad_scheme(relaxed_llm_url):
    from app.core.url_security import validate_public_https_url

    with pytest.raises(ValueError, match="HTTP 或 HTTPS"):
        await validate_public_https_url("ftp://127.0.0.1")


@pytest.mark.asyncio
async def test_strict_mode_rejects_unresolvable_domain(strict_llm_url):
    from app.core.url_security import validate_public_https_url

    with patch(
        "app.core.url_security.socket.getaddrinfo",
        side_effect=socket.gaierror("not found"),
    ):
        with pytest.raises(ValueError, match="无法解析"):
            await validate_public_https_url("https://missing.example")
