# vuln-verify-expert Claude Code Plugin

`vuln-verify-expert` 是 AutoVerify 的 Claude Code 验证插件，负责把漏洞验证方法论、靶场环境搭建和报告输出能力注入 Agent。Web 平台负责任务管理和运行展示，插件负责验证执行流程。

## 组件

```text
plugins/vuln-verify-expert/
├── .claude-plugin/plugin.json
├── settings.json
├── agents/
│   ├── profiler.md
│   ├── env-builder.md
│   ├── auditor.md
│   ├── reproducer.md
│   ├── reporter.md
│   └── vuln-verify-expert.md
├── skills/vuln-verify/
│   ├── SKILL.md
│   ├── references/
│   ├── scripts/
│   └── tests/
└── skills/run-project-env/
    ├── SKILL.md
    └── references/
```

- `agents/profiler.md`：节点 profile，读 README/依赖建立全景并做 web 门禁。
- `agents/env-builder.md`：节点 env_ready，按画像写靶场配方。
- `agents/auditor.md` / `reproducer.md` / `reporter.md`：白盒、复现、报告。
- `skills/vuln-verify`：利用链推理、验证门禁、证据判定和中文报告规则。
- `skills/run-project-env`：Web 项目识别、隔离环境和启动状态规则。

## Claude Code 加载

在仓库根目录执行：

```powershell
claude plugin validate D:\codeproject\vulnReporter\plugins\vuln-verify-expert
claude --plugin-dir D:\codeproject\vulnReporter\plugins\vuln-verify-expert
```

平台 Worker 使用：

```text
--plugin-dir <repo>/plugins/vuln-verify-expert
--agent vuln-verify-expert:vuln-verify-expert
--output-format stream-json
```

## 运行原则

1. 先读源码，确认用户输入到危险 sink 的真实链路。
2. 通过验证门禁后才向隔离靶场发请求。
3. 证据必须来自真实 HTTP、浏览器、OOB 或响应哈希，禁止伪造。
4. 不写入用户原始源码目录；报告和截图进入任务 workspace/artifact store。
5. 凭据使用平台 `credential_ref`，缺少凭据进入 `needs_credentials`，不交互索取明文。
6. Docker/VM 操作通过受控 Sandbox Runner，不直接访问宿主机 Docker Socket。
7. `bypassPermissions` 只允许在隔离 Worker 中使用，不代表可以访问宿主机。

## 本地检查

```powershell
claude plugin validate D:\codeproject\vulnReporter\plugins\vuln-verify-expert
python plugins\vuln-verify-expert\skills\vuln-verify\scripts\check_deps.py
```

依赖检查脚本是只读预检，不会自动安装 Python 包、浏览器或修改 MCP 配置。

## 平台接口边界

插件通过以下平台能力工作：

- `browser`：Playwright MCP 或 Chrome DevTools MCP。
- `sandbox`：创建、启动、执行、探活、收集产物和销毁隔离环境。
- `credential_store`：按引用注入短生命周期凭据。
- `artifact_store`：保存报告、截图、日志、原始响应和哈希。

具体字段和事件格式见 [.claude/api-contract.md](../../.claude/api-contract.md)。