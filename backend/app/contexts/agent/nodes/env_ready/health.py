"""靶场探活（含 health_check.last_error 隐藏状态）。"""
from __future__ import annotations

import asyncio
import re
from typing import Any

_PROBE_BODY_LIMIT = 65536
_PROBE_SNIPPET_LIMIT = 800

# 端口通了但应用崩溃：PHP Fatal、缺表、Spring Whitelabel、Python traceback、WP 连不上库。
_CRASH_BODY_RE = re.compile(
    r"(?is)("
    r"fatal error:|"
    r"parse error:|"
    r"uncaught (?:\w+ )?exception|"
    r"sqlstate\[\w+\]|"
    r"base table or view not found|"
    r"table ['\"][\w.]+['\"] doesn't exist|"
    r"traceback \(most recent call last\)|"
    r"internal server error|"
    r"whitelabel error page|"
    r"502 bad gateway|"
    r"503 service unavailable|"
    r"504 gateway timeout|"
    r"error establishing a database connection|"
    r"could not connect to (?:the )?database|"
    r"no such table[: ]|"
    r"operationalerror"
    r")"
)

HEALTH_RETRIES = 30
HEALTH_RETRY_SECONDS = 3
HEALTH_SETTLE_SECONDS = 3

_HTTPS_CONTAINER_PORTS = {443, 8443, 9443}


def _read_probe_text(fp: Any, limit: int = _PROBE_BODY_LIMIT) -> str:
    try:
        raw = fp.read(limit) if hasattr(fp, "read") else b""
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _crash_page_reason(body: str) -> str:
    if not body or not _CRASH_BODY_RE.search(body):
        return ""
    snippet = " ".join(body.split())
    if len(snippet) > _PROBE_SNIPPET_LIMIT:
        snippet = snippet[:_PROBE_SNIPPET_LIMIT] + "…"
    return f"首页内容异常: {snippet}"


def _probe_http(url: str, timeout: float = 5) -> tuple[bool, str]:
    """GET 首页：非 5xx 且正文不是崩溃页才算活着（401/404 也算）。

    https 自签证书放宽校验——探活只关心应用是否起来，不校验身份。
    """
    import ssl
    import urllib.error
    import urllib.request

    ctx = None
    if url.startswith("https://"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            code = int(getattr(resp, "status", 200))
            body = _read_probe_text(resp)
            if not (200 <= code < 500):
                return False, f"HTTP {code}"
            reason = _crash_page_reason(body)
            if reason:
                return False, reason
            return True, ""
    except urllib.error.HTTPError as e:
        code = int(e.code)
        if 400 <= code < 500:
            return True, ""
        body = _read_probe_text(e)
        reason = _crash_page_reason(body)
        return False, reason or f"HTTP {code}"
    except Exception as e:  # noqa: BLE001
        return False, f"无 HTTP 应答: {type(e).__name__}"


def _http_alive(url: str, timeout: float = 5) -> bool:
    ok, reason = _probe_http(url, timeout)
    _http_alive.last_error = reason
    return ok


def _probe_scheme_for(container_port: int | None) -> str:
    """容器侧端口暗示入口协议：443/8443/9443 → https，其余 http。"""
    if container_port in _HTTPS_CONTAINER_PORTS:
        return "https"
    return "http"


async def health_check(
    ports: list[int] | None,
    extra_ports: list[int] | None = None,
    container_ports: list[int] | None = None,
) -> tuple[bool, int | None, str]:
    """只对 compose 映射到宿主机的 Web 端口探活，不扫本机 80/8080 等常用口。

    compose up 后先等 HEALTH_SETTLE_SECONDS，再 GET 首页正文：端口通但
    Fatal/缺表/Whitelabel 不算就绪。失败细节写入 health_check.last_error
    供回喂 AI。container_ports 与 ports 等长对位，用于推断入口 scheme。
    返回 (ok, live_port, scheme)。
    """
    health_check.last_error = ""
    ordered: list[int] = []
    schemes: list[str] = []
    seen: set[int] = set()
    cps = list(container_ports or [])
    for idx, p in enumerate(list(ports or []) + list(extra_ports or [])):
        port = int(p)
        if port in seen:
            continue
        seen.add(port)
        ordered.append(port)
        schemes.append(_probe_scheme_for(cps[idx] if idx < len(cps) else None))
    if not ordered:
        health_check.last_error = "无 Web 映射口"
        return False, None, "http"

    if HEALTH_SETTLE_SECONDS > 0:
        await asyncio.sleep(HEALTH_SETTLE_SECONDS)

    primary = ordered[0]
    last_error = ""
    for _ in range(HEALTH_RETRIES):
        if _http_alive(f"{schemes[0]}://127.0.0.1:{primary}"):
            return True, primary, schemes[0]
        reason = getattr(_http_alive, "last_error", "")
        if isinstance(reason, str) and reason:
            last_error = reason
        await asyncio.sleep(HEALTH_RETRY_SECONDS)
    for p, scheme in zip(ordered[1:], schemes[1:], strict=False):
        if _http_alive(f"{scheme}://127.0.0.1:{p}"):
            return True, p, scheme
        reason = getattr(_http_alive, "last_error", "")
        if isinstance(reason, str) and reason:
            last_error = reason
    health_check.last_error = last_error or "无 HTTP 应答"
    return False, None, "http"


def _health_fail_detail() -> str:
    detail = getattr(health_check, "last_error", "")
    return detail if isinstance(detail, str) and detail else "无 HTTP 应答"
