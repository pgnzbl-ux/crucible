# 代码审计发现侧实施方案

> 对应业务规格：`docs/discovery-spec.md`

## 1. 技术边界

- Task/TaskRun 继续作为执行聚合根，`task_type` 区分 discovery 与 verify。
- Finding Context 持有 RawFinding、AlertGroup、Adjudication、ReviewAction、LeadRun。
- Agent Context 持有声明式拓扑、Handoff 契约、扫描/聚类/二审/终认执行器。
- Report Context 持有用户可见的审计报告；报告不直接读取 Redis 队列。
- Context 间仅通过 ID、服务方法和结构化 Handoff 交接。

## 2. 执行策略

1. source/profile 建立源码与画像快照。
2. 三个扫描器独立写 ScanRun 与 RawFinding。
3. cluster/triage 形成可复核 AlertGroup。
4. dispatch 幂等创建 LeadRun，并投递 Redis db0 专用队列。
5. LeadWorker 有界并发复用 AuditNode/ReproduceNode，写回线索状态。
6. report 汇总画像、扫描、线索与终认数据；零确认也生成审计报告。

## 2.1 扫描降噪与 AI 二审入口（分阶段）

对应规格 §2.4 / §2.5 / §6.2。不新增编排节点；降噪在 Finding Context，由 cluster 节点调用。

1. **归一化富化**：Semgrep/Gitleaks/OSV 信号写入 `RawFinding.raw`（confidence、rule_class、called 等）；本阶段不改变 grade/dispatch 行为。
2. **确定性降噪 + C 档**：`denoise.py` 表驱动过滤后再 `cluster_findings`；升级 A/B/F 规则；cluster handoff 输出 `dropped_c_count`。
3. **收紧 AI 入口**：triage 只审 A/B；HypothesisPack 可带证据元数据（非引擎结论）；dispatch 阈值不放宽。
4. **评估与报告**：eval 漏斗与报告增加降噪统计；OSV bypass 仍排除在 triage_precision 外。

非目标：密钥出网核验、Semgrep Pro、新 DAG 节点、OSV Rust call-analysis。

> 实施状态：上述 1–4 已落地（`finding/sarif.py` 富化、`finding/denoise.py`、cluster/triage/hypothesis、eval + discovery 聚合报告 `denoise_funnel`）。

## 3. API 与页面迁移

- 创建任务 API 的 Git 与上传入口统一支持显式 `task_type`。
- Finding API 在所有查询中强制 owner 条件，并返回项目/运行与终认摘要。
- 前端以审计为主语，保留定向验证次级入口。
- 用户进度使用稳定业务阶段；节点 DAG 作为诊断详情。

## 4. 验证

- 后端：schema、finding API、upload discovery、lead aggregate、orchestrator 与权限回归。
- 前端：创建参数、路由、审计优先文案、finding 交互与阶段映射。
- 全量执行 pytest、Vitest、TypeScript 类型检查和 Vite 构建。
