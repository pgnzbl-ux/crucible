"""靶场探活：结果显式携带失败原因，避免并发任务共享隐藏状态。"""
from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

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


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """探活结果。

    迭代时只暴露历史三元组 ``ok, live_port, scheme``，兼容既有调用方；
    失败原因通过 ``reason`` 显式读取，不再挂到 ``health_check`` 函数属性上。
    """

    ok: bool
    live_port: int | None
    scheme: str
    reason: str = ""

    def __iter__(self):
        yield self.ok
        yield self.live_port
        yield self.scheme


def failure_reason(result: Any) -> str:
    """读取显式探活原因；兼容测试或旧扩展返回的三/四元组。"""
    reason = getattr(result, "reason", "")
    if isinstance(reason, str) and reason:
        return reason
    if isinstance(result, (tuple, list)) and len(result) >= 4:
        value = result[3]
        if isinstance(value, str) and value:
            return value
    return "无 HTTP 应答"

# SSLContext 构造会触发 OpenSSL 初始化；在部分精简 worker 镜像的线程中可能阻塞。
# 在模块加载线程一次性创建，后续 urllib 探活可安全复用。
_INSECURE_TLS_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_INSECURE_TLS_CONTEXT.check_hostname = False
_INSECURE_TLS_CONTEXT.verify_mode = ssl.CERT_NONE


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


def _network_error_reason(exc: BaseException) -> str:
    """保留可操作的连接根因，同时限制回喂长度。"""
    detail = " ".join(str(exc).split())[:240]
    kind = type(exc).__name__
    return f"无 HTTP 应答: {kind}: {detail}" if detail else f"无 HTTP 应答: {kind}"


def _failure_priority(reason: str) -> int:
    """正文/HTTP 响应比备用协议的连接失败更值得回喂 AI。"""
    if not reason:
        return 0
    text = (reason or "").lower()
    if "首页内容异常" in reason:
        return 100
    if text.startswith("http "):
        return 80
    if "compose 服务" in reason or "compose 服务" in text:
        return 60
    if "无 http 应答" in text:
        return 20
    return 40


def _probe_http(url: str, timeout: float = 5) -> tuple[bool, str]:
    """GET 就绪入口：2xx/3xx 或鉴权拒绝才算活着。

    https 自签证书放宽校验——探活只关心应用是否起来，不校验身份。
    """
    import urllib.error
    import urllib.request

    ctx = _INSECURE_TLS_CONTEXT if url.startswith("https://") else None

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
        body = _read_probe_text(e)
        reason = _crash_page_reason(body)
        if reason:
            return False, reason
        # 登录保护说明应用入口存在；404/普通 4xx 不能证明目标路由已就绪。
        if code in {401, 403}:
            return True, ""
        return False, f"HTTP {code}"
    except Exception as e:  # noqa: BLE001
        return False, _network_error_reason(e)


def _http_alive(url: str, timeout: float = 5) -> bool:
    ok, reason = _probe_http(url, timeout)
    return ok


async def _probe_http_async(url: str, timeout: float = 5) -> tuple[bool, str]:
    """异步 GET 探活，避免同步 urllib 阻塞 env_ready 事件循环。"""
    try:
        async with httpx.AsyncClient(
            verify=False,
            # 3xx 本身已证明入口存活；不跟随到外部站点，避免误判与额外 SSRF 面。
            follow_redirects=False,
            timeout=timeout,
            trust_env=False,
        ) as client:
            async with client.stream("GET", url) as response:
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    remaining = _PROBE_BODY_LIMIT - len(chunks)
                    if remaining <= 0:
                        break
                    chunks.extend(chunk[:remaining])
                body = bytes(chunks).decode("utf-8", errors="replace")
                code = int(response.status_code)
    except Exception as exc:  # noqa: BLE001
        return False, _network_error_reason(exc)

    reason = _crash_page_reason(body)
    if reason:
        return False, reason
    if 200 <= code < 400 or code in {401, 403}:
        return True, ""
    return False, f"HTTP {code}"


def _probe_scheme_for(container_port: int | None) -> str:
    """容器侧端口暗示入口协议：443/8443/9443 → https，其余 http。"""
    if container_port in _HTTPS_CONTAINER_PORTS:
        return "https"
    return "http"


async def health_check(
    ports: list[int] | None,
    extra_ports: list[int] | None = None,
    container_ports: list[int] | None = None,
    *,
    host_ips: list[str] | None = None,
    preferred_scheme: str | None = None,
    probe_path: str = "/",
    compose_project: str | None = None,
    retries: int | None = None,
    retry_seconds: float | None = None,
    settle_seconds: float | None = None,
) -> HealthCheckResult:
    """只对 compose 映射到宿主机的 Web 端口探活，不扫本机 80/8080 等常用口。

    compose up 后先等 HEALTH_SETTLE_SECONDS，再 GET 首页正文：端口通但
    Fatal/缺表/Whitelabel 不算就绪。失败细节随结果显式返回供回喂 AI，
    避免多个并发靶场通过函数属性互相覆盖。container_ports 与 ports 等长
    对位，用于推断入口 scheme。
    """
    ordered: list[int] = []
    seen: set[int] = set()
    cps = list(container_ports or [])
    ips = list(host_ips or [])
    endpoint_hosts: list[str] = []
    scheme_candidates: list[list[str]] = []
    requested_scheme = (preferred_scheme or "").strip().lower()
    if requested_scheme not in {"http", "https"}:
        requested_scheme = ""
    for idx, p in enumerate(list(ports or []) + list(extra_ports or [])):
        port = int(p)
        if port in seen:
            continue
        seen.add(port)
        ordered.append(port)
        inferred = _probe_scheme_for(cps[idx] if idx < len(cps) else None)
        candidates: list[str] = []
        for candidate in (requested_scheme, inferred, "https" if inferred == "http" else "http"):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        scheme_candidates.append(candidates)
        endpoint_hosts.append(ips[idx] if idx < len(ips) and ips[idx] else "127.0.0.1")
    if not ordered:
        return HealthCheckResult(False, None, "http", "无 Web 映射口")

    settle = HEALTH_SETTLE_SECONDS if settle_seconds is None else settle_seconds
    attempts = HEALTH_RETRIES if retries is None else max(1, int(retries))
    interval = HEALTH_RETRY_SECONDS if retry_seconds is None else max(0, retry_seconds)
    if settle > 0:
        await asyncio.sleep(settle)

    path = probe_path if probe_path.startswith("/") else f"/{probe_path}"
    last_error = ""
    for attempt in range(attempts):
        runtime_ready = True
        if compose_project:
            try:
                from app.contexts.lab import docker_ops
                from app.contexts.lab.service import container_runtime_kind

                containers = await docker_ops.list_containers(compose_project)
                runtime = container_runtime_kind(containers)
                runtime_ready = runtime == "running"
                if not runtime_ready:
                    last_error = f"Compose 服务未全部就绪(runtime={runtime})"
            except Exception as exc:  # noqa: BLE001
                runtime_ready = False
                last_error = f"读取 Compose 服务状态失败: {type(exc).__name__}"
        if runtime_ready:
            for port, host, candidates in zip(
                ordered, endpoint_hosts, scheme_candidates, strict=True
            ):
                for scheme in candidates:
                    # IPv6 host 需要方括号；urlsplit 用来避免重复包裹。
                    display_host = host
                    if ":" in host and not host.startswith("["):
                        display_host = f"[{host}]"
                    url = f"{scheme}://{display_host}:{port}{path}"
                    alive, reason = await _probe_http_async(url)
                    if alive:
                        return HealthCheckResult(True, port, scheme)
                    if isinstance(reason, str) and reason:
                        candidate = (
                            f"{urlsplit(url).scheme}://{display_host}:{port}: {reason}"
                        )
                        current_reason = last_error.split(": ", 1)[-1]
                        if _failure_priority(reason) > _failure_priority(current_reason):
                            last_error = candidate
        if attempt + 1 < attempts and interval > 0:
            await asyncio.sleep(interval)
    return HealthCheckResult(
        False,
        None,
        "http",
        last_error or "无 HTTP 应答",
    )
