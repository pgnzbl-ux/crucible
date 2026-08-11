# Deployment Surface — read this BEFORE you trust transport/channel claims

Many "vulnerabilities" look real in source code but vanish at the transport layer, and
the reverse is also true: a chain that looks incomplete in code becomes exploitable
because the deployment shape changes which defenses fire. **Always read this checklist
before Phase 3** — these are the questions scanners and audit reports almost never
state explicitly, but which decide whether `curl` returns 200 or 302.

This file is part of Phase 2 Exit Checklist (item 4). Skipping it is the leading cause
of false negatives like "未复现 / 环境依赖" when the chain is actually reproducible in
the shape real users see.

The point is **transport agnostic**: HTTPS is one common case but the same questions
apply to HTTP-only, gRPC, WebSocket, MQTT, AMQP, gRPC-web, and any other protocol
that the audit chain depends on.

---

## 1. The 7 questions every audit chain depends on

For every audit claim, answer these before sending the first attack request:

1. **Which transport does the user actually speak?** HTTP/1.1, HTTP/2, HTTP/3 (QUIC),
   gRPC, gRPC-web, WebSocket, MQTT, AMQP, raw TCP — these have different upgrade
   mechanics, different header rules, different framing.
2. **What does the listener inventory say?** Read the actual server config
   (`server.xml` / `nginx.conf` / `caddyfile` / `k8s Ingress` / `traefik.yml` /
   `envoy.yaml`) yourself, not the audit's summary.
3. **Where does TLS terminate?** Inside the app server (a JSSE connector), at a reverse
   proxy (Nginx / Caddy / Traefik / Cloudflare / ALB), or at a service mesh sidecar.
4. **Does the app's transport-secure check see TLS?** For Java it's
   `request.isSecure()`. For Node/Express it's `req.secure` (depends on `trust proxy`).
   For Flask it's `request.is_secure` (depends on `SECURE_PROXY_SSL_HEADER`). For PHP
   it's `$_SERVER['HTTPS']` (depends on `fastcgi_param HTTPS on`).
5. **Does the audit's "channel check" assumption hold in your target?** Spring Security
   `requires-channel="https"` blocks every request before the access decision; CORS
   `Access-Control-Allow-Origin: *` with credentialed requests blocks before the
   handler; SameSite cookie handling differs in HTTP vs HTTPS first-load.
6. **Are there reverse proxies / CDN / WAF in the path that strip / add headers?**
   Stripping `X-Forwarded-For`, `X-Forwarded-Proto`, `Authorization`, `Cookie` may
   break the audit chain or, conversely, **expose it** by injecting client-controlled
   copies.
7. **What is the trusted proxy range?** If `RemoteIpValve` / `trust proxy` trusts
   client-supplied headers (e.g. a public listener that fronts the trusted proxy),
   any `X-Forwarded-Proto: https` from the attacker may bypass channel checks.

---

## 2. Reading the actual deployment

### 2.1 Java web apps (Tomcat / Jetty / WildFly / JBoss)

Read the deployed `server.xml` (or equivalent). Things to verify yourself:

- Which `<Connector>` are **inside** vs commented out? A commented-out TLS connector
  means the server doesn't speak HTTPS — Spring Security `requires-channel=https`
  will block every request before the servlet is reached.
- `redirectPort="8443"` on the HTTP connector — Spring Security uses this when the
  channel-check fails, redirecting to `https://host:8443/...`. If 8443 isn't listening,
  the user gets connection-refused after a 302.
- `proxyName` / `proxyPort` / `scheme="https"` on the connector — required when a
  reverse proxy terminates TLS in front, so the servlet learns the original scheme.
- `RemoteIpValve` — if present, Tomcat trusts `X-Forwarded-Proto` from the proxy and
  `request.isSecure()` returns based on that header. If the proxy is misconfigured to
  forward client-controlled `X-Forwarded-Proto`, an attacker can bypass
  `requires-channel`.

**Spring Security channel order (4.x / 5.x):**

1. `ChannelProcessingFilter` runs **before** `AccessDecisionManager`. If `requires-channel`
   fails, the request is rejected (302 to HTTPS URL OR `InsufficientAuthenticationException`
   leading to `AuthenticationEntryPoint.commence`).
2. Only after channel passes does `FilterSecurityInterceptor` evaluate
   `access=permitAll / hasRole(...)`.

> **Implication:** `access=permitAll,method=GET` does **NOT** mean
> "GET is reachable on HTTP". On an HTTP-only deployment, channel-check fires first,
> sends 302 → `https://host:8443/...`, and the test fails because 8443 is dead. The
> *real* test requires HTTPS or a `RemoteIpValve` honoring `X-Forwarded-Proto` from a
> trusted proxy.

**Servlet-spec `<security-constraint><transport-guarantee>CONFIDENTIAL</transport-guarantee>`**
operates at the container level and forces HTTPS independent of Spring Security. If
the target has BOTH `<security-constraint transport-guarantee>` AND a Spring Security
chain, the test container must satisfy both — usually by enabling HTTPS, not by trusting
the proxy alone.

### 2.2 Reverse proxies (Nginx / Apache httpd / HAProxy / Caddy / Traefik / Envoy)

TLS termination:

- Proxy terminates TLS, then forwards the request to the backend. To the backend, the
  request looks like plain HTTP — **unless** the proxy adds `X-Forwarded-Proto: https`
  AND the backend trusts it.
- Failure modes:
  - Proxy forwards header, backend doesn't trust → backend thinks HTTP → channel-check
    blocks → 302 loop or 401.
  - Proxy terminates TLS but doesn't send header → backend always thinks HTTP.
  - Proxy honors **client-controlled** `X-Forwarded-Proto` (i.e. forwards whatever
    the attacker puts there) → `request.isSecure()` is attacker-controlled →
    `requires-channel=https` is bypassable by adding the header. **This is a real
    auth bypass.**

Look for `proxy_set_header X-Forwarded-Proto $scheme` (Nginx),
`RequestHeader set X-Forwarded-Proto "https"` (Apache), `forwardedHeaders: 'X-Forwarded-Proto'`
in AKS Application Gateway — and confirm the backend only trusts that header from
specific proxy IPs, not the public internet.

`proxy_pass` chain: sometimes two proxies deep (e.g. `Cloudflare → Nginx → Tomcat`).
Walk the full chain before claiming "in production, this works as expected."

WebSocket / HTTP/2 / long-poll upgrades: if the audit mentions WebSocket or
`upgrade h2`, the proxy MUST support and configure those upgrades, or the request
silently downgrades to plain HTTP/1.1 and any `wss://`-only defense evaporates.

### 2.3 PHP / Python / Node / Go servers

Different defaults but the same questions:

- **PHP-FPM + Nginx:** Nginx sets `fastcgi_param HTTPS on;` to pass scheme to PHP.
  Absent: `$_SERVER['HTTPS']` is empty regardless of the actual scheme.
- **Node / Express:** `app.set('trust proxy', true)` makes `req.secure` respect
  `X-Forwarded-Proto`. Default is untrusted → doesn't adapt to an HTTPS front.
- **Flask / Django:** configure `SECURE_PROXY_SSL_HEADER` in settings.
- **Go net/http:** has no built-in awareness of forwarders; you must read
  `X-Forwarded-Proto` manually if your handlers depend on it.
- **gRPC backend with grpc-gateway:** the HTTP/1.1 frontend and gRPC backend may trust
  different metadata — verify.

SameSite cookies: `Secure` flag is dropped by Chromium if the response is not HTTPS —
make sure the test container's response is HTTPS end-to-end, or the cookie's
Secure flag is meaningless.

---

## 3. Documenting the deployment shape

In §5.1 of every report, write a one-line **deployment self-declaration** that names
every listener the test exercises — scheme, port, TLS termination point, transport-
secure detection mechanism.

> Connector list (test harness): `0.0.0.0:8088 → Tomcat Connector http-nio-8080`
> (HTTP), `0.0.0.0:8444 → Tomcat Connector https-openssl-nio-8443`
> (HTTPS, self-signed JKS).
> TLS terminated at the Tomcat connector, not at a proxy. `requires-channel="https"`
> in `spring-security.xml` is satisfiable on port 8444.

> Listener inventory (production): `Nginx 1.24 on web-1:443 (TLS terminated)` →
> `proxy_pass http://10.0.1.7:8080` with `proxy_set_header X-Forwarded-Proto $scheme`
> → Tomcat 9 with `RemoteIpValve` trusting `10.0.0.0/8`.

> gRPC-Web reach: `envoy 1.29 on edge:8443` (TLS) →
> `grpc_web filter` → `cluster: app:8080` (plaintext gRPC).
> CORS preflight is handled by envoy, not the application.

If you cannot describe the deployment shape from the live target, the report's
reproduction cannot be reproduced by anyone else. Fix that first.

---

## 4. Common failure modes by transport

| Transport | Common mistake | What actually happens |
|-----------|----------------|----------------------|
| HTTP/HTTPS via Spring | assume `permitAll` means "reachable" | channel filter fires first → 302 |
| WebSocket | assume `ws://` and `wss://` are interchangeable | proxy / `SameSite Secure cookie` semantics differ |
| gRPC | assume metadata header propagation is automatic | header case / framing rules differ; reflection probes differ |
| HTTP/3 (QUIC) | assume `h3` cipher suites | Alt-Svc / connection migration breaks some `request.secure` checks |
| MQTT over TLS | assume 8883 ↔ 1883 same defaults | broker ACL on TLS listener may differ |
| File-based IPC | treat as "network" | bypass reverse-proxy / TLS checks entirely |

For every non-HTTP transport, the deployment-surface questions still apply; only the
specific labels change (port / security policy on the broker / `metadata` headers in
gRPC).

---

## 5. Quick checklist before Phase 3

- [ ] Read the target's listener / connector config yourself (server.xml, nginx.conf,
      caddyfile, systemd unit, k8s Ingress, sidecar manifest).
- [ ] Confirm there is at least one transport-equivalent path the application actually
      reaches (HTTP or HTTPS, with or without `RemoteIpValve` / `trust proxy`).
- [ ] If the audit assumes HTTPS-only behavior, confirm either (a) TLS terminates
      inside the app, (b) `RemoteIpValve` / `trust proxy` honors a trusted proxy, or
      (c) you have pointed the test container's TLS connector at a real cert.
- [ ] If a reverse proxy is in front, confirm `X-Forwarded-Proto` (or equivalent) is
      both set by the proxy AND trusted by the backend — and that the trusted proxy
      range is not the public internet.
- [ ] For non-HTTP transports, find the equivalent for the seven questions in §1.
- [ ] In §5.1 of the report, list every listener and where the channel-check is
      actually satisfied.

Skipping this list is the single most common reason a report says "未复现" when the
chain is actually reproducible, and the most common reason an "已确认" report is
actually unreachable in production.
