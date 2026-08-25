# 代码审计发现侧实施方案

> 对应业务规格：`docs/discovery-spec.md`

## 1. 技术边界

- Task/TaskRun 继续作为执行聚合根，`task_type` 区分 discovery 与 verify。
- Finding Context 持有 RawFinding、AlertGroup、Adjudication、ReviewAction、LeadRun。误报组不长期占 `alert_groups`。
- Agent Context 持有声明式拓扑、Handoff 契约、扫描/聚类/二审/终认执行器。合格门为 Finding Context 纯函数，`dispatch` 与 `LeadStreamer` 共用。
- 合格线索 + 终认队在 Redis db3（`REDIS_CLUE_URL`）；事件/槽位仍 db0，Celery 仍 db1/2。
- Report Context 持有用户可见的**聚合审计报告**；另在 Finding/Lead 路径上为每个 `confirmed|partial|code_reachable` 线索生成**独立漏洞报告**（discovery-spec §11.1）。报告不直接读取 Redis 队列，读 LeadRun 终态与节点漏斗计数。
- Context 间仅通过 ID、服务方法和结构化 Handoff 交接。

## 2. 执行策略

1. source/profile 建立源码与画像快照。
2. 三个扫描器独立写 ScanRun 与 RawFinding（本期不把 raw 搬进 Redis）。
3. cluster 形成工作集 AlertGroup；screen/triage 按 T0–T3 裁剪。T2 只允许定 fp 或升级 T3。
4. dispatch 按 §2.7 合格门幂等创建 LeadRun，投递 Redis db3 队列（禁止复用 db0）。
5. LeadWorker 有界并发复用 AuditNode/ReproduceNode。无 `target_url` 时只跑 audit；落 `verification_basis`（`lab`|`code_path`）。
6. 仅 `confirmed/partial/code_reachable` 长期回写 Postgres 线索台，并**生成一漏洞一份独立报告**；终认证伪与误报只进漏斗。
7. `env_ready` 失败返回 `{ok:false, target_url:null}` 且节点 `completed`，不得杀整任务。
8. 聚合 report 汇总画像、扫描、漏斗与终认清单，并索引/嵌入各漏洞报告；`code_reachable` 进正文；零确认也生成审计总报告。

## 2.1 扫描降噪与 AI 二审入口

对应规格 §2.3.1 / §2.4 / §2.5 / §2.7 / §6.2。不新增编排节点；降噪在 Finding Context，由 cluster 节点调用。

1. **归一化富化**：Semgrep/Gitleaks/OSV 信号写入 `RawFinding.raw`。
2. **确定性降噪 + C 档**：`denoise.py` 表驱动过滤后再 `cluster_findings`。
3. **收紧 AI 入口**：triage 只审 A/B；HypothesisPack 可带证据元数据（非引擎结论）；T3/`api_hunt` 输出必填 `summary`/`reasoning`。
4. **OSV**：bypass + 模板叙事；仅 `called=true` 可入终认。
5. **合格门**：T3 schema 强制 why/evidence/叙事/三布尔；dispatch 删除 A 级+Web 硬门槛。
6. **评估与报告**：漏斗与线索台同一套数；终局漏洞报告结构四来源一致。

非目标：密钥出网核验、Semgrep Pro、把 2530 条 raw 或 cluster 工作集整体迁 Redis、回填禅道旧瘦字段。

## 2.2 Redis db3 配置配套

`config.py` 对连接串无代码默认值，漏配即启动失败。

- `backend/.env.example` / 本地 `backend/.env`：`REDIS_CLUE_URL=redis://:crucible_redis@localhost:6380/3`
- `infrastructure/.env.example` 注释列出 db0/1/2/3
- pytest 注入 `REDIS_CLUE_URL`；SSOT 断言字段清单
- 线索客户端超时对齐 `lead_queue`（2s/5s）
- compose 不用新容器：同一 Redis 实例开 db（默认 `databases 16`）
- 崩溃恢复靠 db3 TTL + 已有 Lead 队列补偿；Redis 需 AOF/RDB（compose 已有 `redisdata`）

## 2.3 API 清单 + 猎洞（补 Semgrep 鉴权/逻辑短板）

对应规格 §4.2.4 / §6.2.1；调研见 [`research-api-hunting.md`](research-api-hunting.md)。

1. **`api_inventory`**：`requires=(source, profile)`，与 `scan_*` / `env_ready` 同波；按画像语言表驱动 parser（OpenAPI 恒跑；Python/Node/PHP/Java/Go 各有路由或文件入口解析）。产出 BOM + resource_key。
2. **`api_hunt`**：`requires=(api_inventory,)`，与 `cluster→screen→triage` 并列；嫌疑经 §2.7 **全部门**（含 conf≥阈值）直出 Adjudication，绕过 screen/triage；`cluster` 只吃三扫描引擎；`screen`/`triage` 排除猎洞组；`dispatch.requires=(triage, api_hunt)`。
3. **VERIFY_MODE** skip inventory/hunt；失败隔离：hunt `ok=false` 不阻断扫描复核链。
4. **扩展栈**：只加确定性 parser；禁止 AI 列端点。动态前缀等解不出的只记 incomplete，不让模型编 path。

## 3. API 与页面迁移

- 创建任务 API 的 Git 与上传入口统一支持显式 `task_type`。
- Finding API 默认 `scope=workbench`；对外文案「可疑真洞 / 误报 / 二审未决」。
- 前端线索台主队列：验证中 + 已确认 + 代码可达。去掉已降噪/待复核主入口；去掉误报复活按钮。
- 用户进度使用稳定业务阶段；节点 DAG 作为诊断详情。
- **漏洞报告页**：一级导航改为「漏洞报告」；页内 Tab「审计报告 | 验证报告」。审计报告先按 discovery 任务（项目/版本）列表，再进任务内单漏洞报告列表；验证报告保持一次 verify 一份。

## 4. 验证

- 后端：合格门表驱动、fp 不落库不展示、T2/传播 tp 不入队、T3 schema 拒收、env_ready 降级、报告对账、finding API、orchestrator 与权限回归。
- 前端：队列文案、禁止裸 `tp`/`fp`、TypeScript 类型检查。
- 禅道口径回归：不得把快审 tp 全送终认。
- 全量执行相关 pytest 与 `npx tsc --noEmit`。
