# vuln-verify-expert Claude Code Plugin

桌面端 Claude Code 插件：白盒优先的漏洞验证方法论。在本机用 `claude --plugin-dir` 加载即可独立跑。

Crucible 平台 **不** 把本目录打进 agent-runner。平台运行时用的是 `infrastructure/agent-runner/node-skills/` 里按节点蒸馏的 skill。本插件是母本 / 指导材料。

## 组件

```text
plugins/vuln-verify-expert/
├── .claude-plugin/plugin.json
├── settings.json
├── agents/
│   └── vuln-verify-expert.md    # 桌面端总编排（阶段 0/A/B/C/D）
├── skills/vuln-verify/
│   ├── SKILL.md
│   ├── references/
│   └── scripts/
└── skills/run-project-env/
    ├── SKILL.md
    └── references/
```

- `agents/vuln-verify-expert.md`：桌面会话的阶段化工作流。
- `skills/vuln-verify`：利用链、验证门禁、证据判定、中文报告。
- `skills/run-project-env`：Web 识别、隔离环境、启动排障。

## Claude Code 加载

在仓库根目录：

```powershell
claude plugin validate plugins/vuln-verify-expert
claude --plugin-dir plugins/vuln-verify-expert
```

```text
--plugin-dir <repo>/plugins/vuln-verify-expert
--agent vuln-verify-expert:vuln-verify-expert
```
