# 工程治理规范

> 本目录收录 Crucible 项目的工程治理规范，源自 `ai-coding-best-practice` Harness 资产包（`.wiscode/harness/ai-coding-best-practice/governance/`）。

## 文件

- `constitution.md` — 项目开发宪法（真理 / 测试先行 / 演进 / 极简 / 质量安全 / 责任共担）
- `AGENTS.md` — AI Agent 协作标准与工作流（内部执行规范）

## 裁决链

`constitution.md > AGENTS.md > spec.md > plan.md > tasks.md > 既有代码`

业务行为以 `spec.md` 为唯一事实来源，技术实现以 `plan.md` 为唯一事实来源，代码必须同时服从已评审文档。

## 维护说明

治理文件由 Harness 资产包提供源版本。如需变更治理条款，先修改源资产包（`.wiscode/harness/ai-coding-best-practice/governance/`），再同步本目录，保持两份一致。
