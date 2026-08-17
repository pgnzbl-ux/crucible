# 平台安全与可靠性加固设计

## 1. 背景与目标

本轮修复覆盖全项目审查确认的六项可兼容修复缺陷：LLM Provider 未鉴权与 SSRF、Task 越权、Report/Evidence 越权、Celery 提交前投递、不可信 Compose 宿主执行、Lab 创建失败后资源残留。Agent Runner 宿主网络访问与现有 Lab 复现链路冲突，按用户确认保留并记录为剩余风险。

目标是在不引入 RBAC、域名白名单、Transactional Outbox 或出站代理的前提下，封闭已确认的攻击与故障路径，同时保留任意公网 LLM Provider 和常见开源项目 Compose 的兼容性。

## 2. 已确认约束

- LLM Provider 管理接口本轮要求登录，不新增管理员角色。
- Base URL 不使用域名白名单；允许任意公网域名。
- Base URL 必须是 HTTPS 域名，禁止 IP 字面量；解析结果必须全部为公网地址。
- 拒绝回环、私网、link-local、保留、多播、未指定地址，包括 Docker 和宿主机网段。
- LLM 连接不跟随重定向。
- Compose 采用高危字段拒绝策略，不采用严格字段白名单。
- Celery 采用“数据库显式提交后投递”，不新增 Outbox。
- Runner 不做全局私网封锁，也保留 `host.docker.internal`，因为 reproduce 依赖宿主映射端口访问 Lab；依靠 Provider URL 校验封闭 LLM Base URL 内网入口。

## 3. API 鉴权与所有权

### 3.1 LLM Provider

`/settings/llm/**` 所有读取和写入端点注入 `CurrentUserId`。Provider 仍是平台全局配置，任意已登录用户可管理；这是 RBAC 落地前的显式临时策略。

### 3.2 Task

任务详情、事件历史、SSE、取消、重试、删除和节点运行记录均必须按 `(task_id, owner_id)` 查询。资源不存在和 owner 不匹配统一返回 404，避免枚举。

### 3.3 Report 与 Evidence

报告详情、按任务取报告、发布、导出和证据列表均按 owner 查询。证据上传取消开发环境宽松分支，所有环境执行相同 owner 校验。

## 4. LLM Base URL 防护

新增共享的异步 URL 校验函数：

1. URL scheme 必须为 `https`。
2. 必须存在 hostname，且 hostname 不能是 IP 字面量。
3. 禁止 userinfo、fragment 和非法端口。
4. 使用系统 DNS 解析 hostname；解析失败即拒绝。
5. 所有解析地址都必须是公网（`ip.is_global`），或属于 TUN fake-ip 网段 `198.18.0.0/15`（Clash/Surge 等本地代理常用；该段不路由到真实内网主机，放行以免误杀开发机 TUN）。仍拒绝 RFC1918、回环、链路本地、链路元数据与 CGNAT。
6. create、update、临时 test、已保存 Provider test，以及构造 Runner 环境前都调用同一校验。
7. `httpx.AsyncClient` 显式设置 `follow_redirects=False`。

该方案不承诺抵御解析校验与实际连接之间的 DNS rebinding；完整防护需要出站代理或网络层策略，不在本轮范围。

## 5. Celery 投递一致性

创建和重试任务时，先 flush 并显式 commit Task/TaskRun，再调用 `send_task`。投递失败后把 Task 和对应 TaskRun 标记为 failed，写入可观察错误并再次 commit，然后向 API 返回明确失败。

该方案消除 worker 在事务可见前消费的竞态，但不提供数据库提交后进程崩溃的最终投递保证；该保证需 Outbox，不在本轮范围。

## 6. Compose 准入策略

在任何 `docker compose up` 前解析 Compose 文件并拒绝：

- `privileged: true`
- `network_mode: host`、`pid: host`、`ipc: host`
- `devices`
- `cap_add`
- Docker/Containerd/Podman socket 挂载
- bind mount 源路径越出 Lab 工作目录
- build context 或额外 Dockerfile 路径越出 Lab 工作目录
- `/proc`、`/sys`、`/dev` 等宿主敏感路径挂载

正常镜像、服务依赖、环境变量、命名卷、端口和 Compose 网络继续允许。解析失败按不安全配置处理，禁止执行。

## 7. Runner 网络剩余风险与 Lab 补偿

- 保留 Agent Runner 的 `host.docker.internal:host-gateway` 映射、公网 DNS和现有可外联 bridge，避免破坏 LLM、Git 与 Lab 访问。
- 通用 Agent 工具仍可能访问宿主映射端口；彻底隔离需要让 Runner 动态加入当前 Lab Compose 网络或引入出站代理，本轮明确不实施。
- Compose 启动成功后，若配方上传或 `mark_ready` 失败，必须执行当前 Lab 项目的 `compose down -v --remove-orphans` 后再标记 failed。
- 周期巡检应清理 failed/destroyed 状态但仍有 Compose 资源的 Lab，作为补偿失败兜底。

## 8. 测试与交付

- 所有修复先增加失败测试，再实现。
- URL 校验和 Compose 策略使用表格驱动测试。
- API 测试覆盖匿名请求、owner A 访问 owner B、合法 owner。
- Celery 测试断言 commit 发生在 send_task 之前，投递失败进入 failed。
- Lab 测试模拟上传失败并断言 compose down。
- 完成后运行后端全量 pytest、前端 typecheck，并同步 `.claude/api-contract.md`、安全规范和开发指导文档。
