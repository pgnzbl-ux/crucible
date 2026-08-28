# 部署面核验 —— 在相信 transport/信道断言之前读

很多"漏洞"在源码里成立，却在 transport 层消失；反之亦然：代码里看起来断掉的链，因部署形态不同而可利用。**本节点没有靶场访问权**——部署面证据来自**仓库内的部署配置**（`server.xml` / `nginx.conf` / `caddyfile` / `docker-compose` / k8s Ingress / `application.yml` 的 server 段），以及 reproduce 节点稍后的实测。跳过本清单是"未复现/环境依赖"误报的首要来源。

Transport 无关：HTTPS 只是常见情形；同样的问题适用于 HTTP-only、gRPC、WebSocket、MQTT、AMQP、gRPC-web。

## 1. 每条审计链都要回答的 7 问（进 reproduce 前完成）

1. **用户实际走哪种 transport？** HTTP/1.1、h2/h3、gRPC、WebSocket、MQTT、AMQP、裸 TCP——升级机制、头规则、帧格式各不相同。
2. **监听器清单说了什么？** 亲自读仓库里的服务配置（`server.xml`/`nginx.conf`/compose `ports`/启动参数），不要转述别处的摘要。
3. **TLS 在哪终结？** 应用内连接器（JSSE）？反代（Nginx/Caddy/Traefik/ALB）？服务网格 sidecar？
4. **应用的 transport-secure 检查能看到 TLS 吗？** Java `request.isSecure()`（`RemoteIpValve`）；Node `req.secure`（`trust proxy`）；Flask `request.is_secure`（`SECURE_PROXY_SSL_HEADER`）；PHP `$_SERVER['HTTPS']`（`fastcgi_param HTTPS on`）。
5. **"信道检查"假设在你的靶场成立吗？** Spring Security `requires-channel="https"` 会在鉴权前拦掉所有请求；servlet `<transport-guarantee>CONFIDENTIAL` 在容器层强制 HTTPS，与 Security 链叠加时靶场必须同时满足。
6. **链路上有剥/加头的反代 / CDN / WAF 吗？** 剥 `X-Forwarded-For`/`X-Forwarded-Proto`/`Authorization`/`Cookie` 可能断链；**注入客户端可控副本反而可能放行**。
7. **可信代理段是什么？** `RemoteIpValve` / `trust proxy` 若信任公网侧可伪造的 `X-Forwarded-Proto`，信道检查可被一个请求头绕过——这是真实的鉴权绕过。

## 2. 读仓库部署配置的要点

### Java（Tomcat/Jetty/WildFly）

- 哪些 `<Connector>` 生效、哪些被注释？注释掉的 TLS 连接器 = 不说 HTTPS → `requires-channel=https` 会先拦一切。
- HTTP 连接器上的 `redirectPort="8443"`：信道检查失败时 302 过去；**8443 没监听 = 用户看到 connection refused**。靶场配方要么真起 TLS 连接器，要么靠 `RemoteIpValve` 认 `X-Forwarded-Proto`。
- **Spring Security 信道顺序**：`ChannelProcessingFilter` 先于 `FilterSecurityInterceptor`。`access=permitAll` ≠ "HTTP 上可达"——HTTP-only 部署下信道检查先 302，测试死于死端口。

### 反向代理（Nginx/Apache/HAProxy/Caddy/Traefik/Envoy）

- 代理终结 TLS 后转发明文给后端，**除非**代理加 `X-Forwarded-Proto: https` 且后端信任它。
- 失败模式：代理发了头后端不信 → 信道检查拦死；代理不发头 → 后端永远当 HTTP；**代理透传客户端可控的 `X-Forwarded-Proto`** → `isSecure()` 攻击者可控 → `requires-channel` 可被加头绕过。
- 二级代理链（Cloudflare→Nginx→Tomcat）逐跳走完再下结论。WebSocket/h2 升级：代理必须显式支持 `Upgrade`，否则静默降级、`wss://` 侧防御蒸发。

### PHP / Python / Node / Go

同一组问题不同开关：Nginx `fastcgi_param HTTPS on`；Express `trust proxy`；Flask/Django `SECURE_PROXY_SSL_HEADER`；Go 手动读 `X-Forwarded-Proto`。SameSite `Secure` cookie 在非 HTTPS 响应下被浏览器丢弃——端到端 HTTPS 才有意义。

## 3. 结论如何落进 output

- 部署面与链路前提**匹配** → 照常走；无法从仓库判定 → 写进 `runtime_dependent=true` 的 `unresolved_facts`（如"仓库仅见 HTTP connector，`requires-channel=https` 是否满足待 reproduce 实测 8443"）。
- 部署面与链路前提**确定不匹配**（链要 HTTPS、仓库只有 HTTP 且无信任代理）→ 这是确定性信息：按 `fail`/结构调整处理，不要让 reproduce 白打一轮。
- `transport_shape` 由平台从 env_ready 配方注入 reproduce/report——你在 output 里不要编造宿主地址，只描述仓库内的 connector/代理事实。

## 4. Phase 3 前速查（交给 reproduce 执行的遗留项写清楚）

- [ ] 仓库内 listener/connector 配置已读（server.xml、nginx.conf、compose、Ingress）
- [ ] 存在至少一条 transport 等价的可达路径（含/不含 RemoteIpValve、trust proxy）
- [ ] 链路假设 HTTPS-only 时：TLS 在应用内终结 / 信任代理成立 / 靶场配方已配 TLS 三者必居其一
- [ ] 有反代时：`X-Forwarded-Proto` 既被代理设置**又**被后端信任，且可信段不是公网
- [ ] 非 HTTP transport：§1 七问的等价问题已答
- [ ] 遗留给 reproduce 的实测项（端口、scheme、302 行为）已写进 `unresolved_facts`
