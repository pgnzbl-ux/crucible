# 每节点蒸馏 skill 注入 runner

> 版本: v1.0 · 2026-08-17
> 状态: 已评审（对话确认：桌面 `plugins/` 为母本；每节点一份蒸馏 skill；system / user 不重复）
> 定位: 修正「把桌面插件整包打进 agent-runner」的运行时模型。

## 0. 对比

| | 现有实现 | 新需求 |
|---|---|---|
| 方法论来源 | `plugins/vuln-verify-expert` 整包 COPY 进镜像 | 桌面插件只当指导母本，不进 runner |
| SDK 加载 | `plugins=` + `--agent vuln-verify-expert:{profiler,…}` | 不加载 Claude Code 插件 |
| system | 插件 agent.md + 自动挂上整份 skill（含其它 Phase） | 该节点蒸馏 SKILL.md，append 到 claude_code preset |
| user | 角色 + 禁令 + 源码路径 + 整份 input_json（与 system 重复） | 只要求 `submit_result` + 本轮 JSON |
| 冲突 | audit 挂整份 vuln-verify（含「发 HTTP」）；profile 挂整份 run-project-env（含 compose up） | 每份 skill 只含本节点职责 |

编排器、`.node.json`、`submit_result` MCP、工具黑白名单、节点 input/output schema **不变**。

## 1. 选定方案

候选：

1. **继续加载桌面插件** — 否。与「母本不当 runtime」冲突。
2. **平台自建 mini plugin + skill 触发** — 否。Claude Code skill 是可选触发，节点方法论必须每次生效。
3. **把 skill 写进仓库 `.claude/skills/`** — 否。会污染 clone 的源码树。
4. **蒸馏 SKILL.md，经 `ClaudeAgentOptions.system_prompt` append** — **采用**。生产 Agent 编排的常规做法：system = 工种 SOP，user = 本单原料。append `claude_code` preset，避免盖掉 SDK 自带的工具用法说明。

注入：

```
system_prompt = {type: "preset", preset: "claude_code", append: SKILL.md 正文}
user message  = "按 system 完成本节点。完成后必须调用 submit_result。\n\n输入(JSON):\n{input_json}"
```

权威分工：

- **形状**：MCP `submit_result` schema + worker `validate_output`
- **语义 / 禁令 / 工作流**：该节点 SKILL.md（只写一次）
- **本轮数据**：user JSON（只出现一次）

## 2. 五份 skill

路径：`infrastructure/agent-runner/node-skills/{profile,env_ready,audit,reproduce,report}/SKILL.md`

| 节点 | system 含 | system 禁 |
|---|---|---|
| profile | 全景字段 + web 门禁 | 写配方、起环境、漏洞结论 |
| env_ready | 写 `.vuln-env`、避开占用口、按 previous_error 改一处 | web 门禁结案、compose up |
| audit | Phase 1/2/2.5、三问语义 | 发 HTTP、8 节报告 |
| reproduce | Phase 3/4/5、证据档位、8 节正文 | 「本节点不发请求」、自己猜 URL |
| report | 误报 8 节、`final_verdict=false_positive` | 打靶场 |

`source` 仍是纯代码，无 skill。

## 3. 插件目录

`plugins/vuln-verify-expert` 恢复桌面形态：只保留 `agents/vuln-verify-expert.md` + 两个 skill。删除为平台加的 5 个子 agent，去掉 skill 里的「Crucible 平台」专章。

## 4. 镜像

Dockerfile `COPY infrastructure/agent-runner/node-skills/` → `/app/node-skills/`。不再 COPY `plugins/`。
