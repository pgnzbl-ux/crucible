# 节点 3 静态白盒门禁设计

> 版本: v1.1 · 2026-08-14
> 状态: 已评审
> 定位: 把 audit 收成真正的静态白盒——只根据漏洞描述 + 源码判定利用链在纸上通不通；平台硬拦截 HTTP；`gate_verdict` 与 `vuln-verify` skill Phase 2.5 对齐后拆成三值。
> 覆盖并修正：`2026-08-12-platform-node-orchestration-design.md` §1.2 出口 B（仅 `fail`）、§1.3 audit 产出（仅 `pass|fail`）；`auditor.md`「不发 HTTP」但平台仍允许 curl/WebFetch。
> v1.1：吸收平台化评审——三值不可机械证伪；uncertain 一次即出口 D（不做 env_ready 式排障循环）；形状不变量；`runtime_dependent`；待复核保留工作区。
> 阅读对象: 编排 / agent-runner 工具策略 / auditor / 任务详情步骤条。

---

## 0. 已确认决策

| 项 | 决定 |
|---|---|
| 节点职责 | 静态白盒：漏洞描述 + 源码。**禁止**打靶场、禁止改源码 |
| 平台 vs 模型 | 三值分类 **不可机械证伪**。平台只校验产出**形状**并按枚举**路由**；不判断 kill_chain 是否为真、也不判断模型是否把 runtime-dependent 错写成 `uncertain` |
| 与 skill 对齐 | audit = Phase 1 / 2 / 2.5；Phase 3+ 属于 reproduce / report。**不**把 skill 拆成两份文件 |
| `fail` | 结构性阻断 / 无安全影响 / 路径证明断开 → skip reproduce，**仍跑 report**，`verdict=false_positive` |
| `pass` | 纸上 payload 通，**或**静态解不开 / runtime-dependent（skill：不确定不是 Quick-Stop，continue 去线上）→ 进 reproduce。必填 `runtime_dependent: bool`，供节点 4 区分打法 |
| `uncertain` | **仅**描述对不上代码、或连具体 HTTP 危害都锁不住。**一次提交即出口 D**：skip reproduce **和** report，`task=needs_review`，`verdict` 空。不在 AuditNode 内重试 |
| 硬拦截 | audit 去掉 Write/Edit/WebFetch；Bash 拒 curl/wget/httpie。节点顺序仍是 env_ready → audit（靶场此时已 live），因此硬拦截是必须项，不是可选 |
| 校验 | 按三值收紧形状；不合格 = 节点失败，不是 `uncertain` |
| 待复核收尾 | 保留 `host_workdir`（与 `failed` 相同）；Lab **不**因 `needs_review` 进入 live、不续 TTL；默认重试从节点 0 整条重跑，也可 `from_node=audit` 只重跑审计及之后 |
| Mock | SDK 关闭时仍交合法 `pass` 样例（含 `kill_chain` / `defense_layers` / `payloads` / `runtime_dependent=false`） |

明确不做：拆 `vuln-verify` 目录、拦 `python urllib`、给 TaskRun 加 `needs_review`、独立审阅工作流、跨任务缓存审计结果、改 6 档最终 verdict、对调 audit / env_ready 节点顺序、把 uncertain 做成多轮排障循环。

---

## 1. 问题与目标

### 1.1 现状偏离

编排设计已是「节点 3 纯白盒，Gate 不通则跳过复现」。实际：

- `auditor.md` 写「本节点不发任何 HTTP」，但 `run_one.py` 对 audit 仍给 Bash `curl` 和 `WebFetch`。
- auditor 加载整份 `vuln-verify` skill，skill 含 Phase 3「发一次 HTTP」。Agent 会把审计和打靶做成同一次会话。
- schema 只强制 `gate_verdict`，交 `{gate_verdict: "pass"}` 就能过，没有 kill_chain。
- `gate_verdict` 只有 `pass|fail`。skill Phase 2.5 把「结构性阻断」和「静态解不开 / 运行时依赖」分开：前者 Quick-Stop 误报，后者 **continue 去 Phase 3**。平台无法表达「描述对不上代码、不该打靶」。
- 节点顺序是 env_ready → audit：靶场在静态审计时已经 live，模型旁边就有活靶，文案禁 HTTP 而工具不禁。

### 1.2 目标闭环

```
audit 一次 run_ai_node（无节点内重试）
    │
    ├─ pass  → 节点完成 → reproduce（读 runtime_dependent）→ report → task=completed
    │
    ├─ fail  → 节点完成 → skip reproduce → 仍跑 report（误报报告）
    │            → task=completed, verdict=false_positive
    │
    └─ uncertain → 节点 completed（output_json 落库）
                   skip reproduce 和 report
                   task=needs_review, verdict 空
                   TaskRun.status=completed（不新增 run 状态）
                   保留 host_workdir；Lab 不占 live
```

节点失败（无 `submit_result` / schema 不合格 / 容器挂）→ `task=failed`，走现有失败路径，**不是** `uncertain`。

uncertain **不是** env_ready 那种可观察排障循环。平台看不见「描述是否真对不上」，只路由模型交上来的枚举。

---

## 2. 与 vuln-verify skill 的映射

权威方法论仍是 `plugins/vuln-verify-expert/skills/vuln-verify/SKILL.md`。本设计不改写 Phase 3–5 的证据纪律，只规定 **平台节点 3 停在 2.5**，并把 Gate 答语接到 `gate_verdict`。

| Skill 原意 | `gate_verdict` | `runtime_dependent` | 平台动作 |
|---|---|---|---|
| Q1 无安全影响；Q2 路径证明断开；Q3 结构性阻断（纸上没有任何 payload 能活） | `fail` | 不要求 | 出口 B：误报 |
| 纸上 payload 通 | `pass` | `false` | 去 reproduce，按候选 payload 确认 |
| 静态解不开 / runtime-dependent / 畸形 PoC（skill：**不是** Quick-Stop，proceed live） | `pass` | `true` | 去 reproduce；节点 4 按 `gate_reason` 当运行时依赖处理，不是当「已通的 payload」 |
| 锁不住具体 HTTP 危害；漏洞描述对不上仓库里任何入口/sink | `uncertain` | 不要求 | 出口 D：待复核，不打靶 |

Agent 禁止把「我不是 100% 确定但路径可能连得上」写成 `uncertain`——那是 skill 的 continue，必须 `pass` + `runtime_dependent=true`。平台 **不**强制执行这条语义，只在文案里写；形状上 `uncertain` 不得带 payloads（见 §3）。

`reproduce` 仍负责：live HTTP、变体、5 轮运行时条件、`not_reproduced`。出口 D **不是** skill 决策树里「5 轮未复现」。

---

## 3. 产出契约

`ai_runner` 的 audit schema 与 `submit_result` 工具定义同步。编排器路由只读 `gate_verdict`。reproduce 的 input 仍是整份 audit 产出，**必须能读到** `runtime_dependent` 与 `gate_reason`。

一律必填：`gate_verdict`（`pass` \| `fail` \| `uncertain`）、`gate_reason`（非空字符串）。

| 值 | 形状（平台可校验） | 禁止 | `gate_reason` 写什么 |
|---|---|---|---|
| `pass` | `kill_chain` 非空；`defense_layers` 为数组（允许 `[]`）；`payloads` 长度 ≥ 1；`runtime_dependent` 为 bool | — | Q1/Q2/Q3 各一句；`runtime_dependent=true` 时写清缺哪项运行时事实 |
| `fail` | `kill_chain` 非空；`defense_layers` 为数组且 **长度 ≥ 1**（不校验「是不是阻断那一层」） | — | 结构性原因 |
| `uncertain` | `gate_reason` 非空即可；`kill_chain` / `defense_layers` 有则落库 | **`payloads` 必须缺省或 `[]`**。非空 → 校验失败（已经构造出 payload 就必须 `pass`） | 缺什么：对不上哪段描述、为何锁不住 harm |

不新增 `reason_code` 枚举。

校验失败 → `run_ai_node` 抛错 → 整节点失败 → `task=failed`。不把校验失败收成 `uncertain`。

---

## 4. AuditNode

编排器对 audit `execute` **一次**；节点内 **一次** `run_ai_node`。不仿 `EnvReadyNode` 做 attempt 循环。

- `input_json`：现有 `source_path` / `vulnerability_description` / `profile`
- **不注入** `target_url` / `initial_creds`
- **不注入** `attempt` / `previous_error`（没有节点内重试）
- 返回通过校验的产出；`pass` / `fail` / `uncertain` 都是 `node_run.status=completed`

断点续跑：audit 已 `completed` 则复用 `output_json`，按 §5 走出口，不重跑。若已完成且值为 `uncertain`，必须走出口 D，不得进 reproduce。

用户从 `needs_review` 点重试：默认仍从节点 0 整条重跑；也可 `from_node=audit` 只重跑审计及之后（拷贝 source/profile/env_ready）。env_ready 可走已落地的 MinIO 配方短路。

---

## 5. 编排器出口

保留出口 A（非 web）、B（`fail`）、C（env_ready 5 轮失败）。新增出口 D。

| 出口 | 触发 | 跳过 | task | verdict |
|---|---|---|---|---|
| B | `gate_verdict=fail` | reproduce | `completed` | `false_positive`（report 的 `final_verdict` 不得覆盖） |
| D | `gate_verdict=uncertain` | reproduce **和** report | `needs_review` | 空 |

实现：audit 返回后，除现有 `_skip_reproduce_if_gate_fail` 外，增加 `_skip_after_uncertain_audit`：把 reproduce、report 标 `skipped`（已 completed 的不改）。for 循环后续看到 `skipped` 则 continue。循环结束后：若 audit 为 `uncertain`，`task.status=needs_review`，`run.status=completed`，`task.verdict=None`，return `{status: "needs_review", verdict: None}`。

`tasks.py` 收尾已认识 `needs_review`（报告归档条件含该状态）；本轮 **不** 为 D 生成报告。

### 5.1 待复核时的资源

| 资源 | 行为 |
|---|---|
| `host_workdir` | **保留**。`should_retain_hostdir` 增加 `needs_review`（与 `failed` / `cancelled` 并列）。人要对着源码看「描述对不上」 |
| agent-runner 容器 | 仍拆（现有 `teardown_task_runtime`） |
| Lab | `LIVE_TASK_STATUSES` **不**含 `needs_review`，不续 TTL。到期仍可静默 `expired`。重试时再 `acquire`（配方可 MinIO 命中） |

---

## 6. 工具策略（`run_one.py`）

节点顺序保持 source → profile → **env_ready → audit** → reproduce → report。audit 不吃 env_ready 产出，但靶场已 live。skill 的 deployment-surface 检查会诱使模型碰活靶。因此：

仅 `NODE_KEY=audit`：

**allowed_tools**：`Read` / `Grep` / `Glob` / `Bash` / `WebSearch` / `submit_result`。  
去掉 `Write`、`Edit`、`WebFetch`。profile 仍无 Bash；env_ready / reproduce / report 名单不变。

**Bash**：全局黑名单之后，audit 额外 deny `curl` / `wget` / `httpie`（表驱动，模式对齐 `ENV_READY_DENY_RES`）。deny 事件仍打 `tool.call.denied`。

不拦 `python -c urllib`（已知残差，与 env_ready 一样不把 hook 做成万能防火墙）。本轮不对调节点顺序来消掉活靶。

---

## 7. 插件文案

### 7.1 `agents/auditor.md`

加「Crucible 平台」专章（口吻对齐 `env-builder.md`）：

- 只做 Phase 1 / 2 / 2.5；禁止进入 Phase 3
- 三值含义（§2 表）；runtime-dependent 必须 `pass` + `runtime_dependent=true`，禁止写成 `uncertain`
- 已构造出 payload 就必须 `pass`；`uncertain` 不得带 payloads
- 禁止 HTTP / 禁止改源码；平台会拦 curl 与 Write。旁边的靶场由平台维护，本节点不要去打
- `uncertain` 由平台直接待复核，agent 不要自己循环

`submit_result` 字段表与 §3 一致。`maxTurns` 保持 100。

### 7.2 `skills/vuln-verify/SKILL.md`

在文首或 Phase 2.5 后加一小节 **「Crucible 平台：节点 audit 停在 2.5」**，写清三值 + `runtime_dependent` 映射，并声明 Phase 3–5 由节点 reproduce/report 执行。  
**不**删除 skill 里的 Phase 3 正文（本地 Claude / reproduce 仍要用）。

不拆 skill 目录。加载整份 skill 的残余风险由 §6 工具闸门兜住，不指望这一小节替代拆文件。

---

## 8. 前端（最小）

`summarizeNodeOutput('audit')`：

- `fail`：保持 `Gate 失败 · {gate_reason}`
- `pass` 且 `runtime_dependent===true`：`Gate 通过 · 运行时依赖`
- 其余 `pass`：`Gate 通过`
- `uncertain`：`待复核 · {gate_reason}`

非 compact 的 audit 行额外展示 `gate_reason` 与 `kill_chain`（有则显示），保证 `needs_review` 时详情页能读到静态结论。工作区仍在，但不做独立审阅页。

---

## 9. 文档同步（实施时）

- `2026-08-12-platform-node-orchestration-design.md` §1.2 增出口 D；§1.3 audit 产出改为三值 + `runtime_dependent` + 形状约束
- `docs/agent-workflow.md` 误报 Quick-Stop 旁注明平台出口 D
- `docs/development-guide.md` 仅当清单需要点名本闭环时改一句，不新开 P0

---

## 10. 测试范围

先红后绿。至少覆盖：

| 用例 | 期望 |
|---|---|
| 编排 `fail` | reproduce skipped，report 执行，`false_positive` |
| 编排 `uncertain` | 只跑一轮 AI；reproduce+report skipped，`task=needs_review`，audit `output_json` 含 `gate_reason` |
| 编排 `pass` + `runtime_dependent=true` | 进 reproduce；input 含该字段 |
| 续跑：已 completed 的 uncertain | 不重跑 audit，走出口 D |
| schema 表驱动 | pass 缺 payloads / 缺 `runtime_dependent`；fail 的 `defense_layers` 非数组或长度 0；uncertain 缺 gate_reason；**uncertain 带非空 payloads** → 校验失败 |
| `should_retain_hostdir("needs_review")` | `True` |
| Bash | `curl` 在 audit deny、在 reproduce allow；`npm` 在 audit 仍 allow（env_ready 才拒） |
| allowed_tools | audit 选项不含 WebFetch / Write |
| 前端 `nodeOutput` | `uncertain` 摘要含待复核；`runtime_dependent` pass 摘要含运行时依赖 |

不测「第 2 轮才 pass」——没有节点内重试。

---

## 11. 验收

- 真实 SDK 下，audit 容器 `curl` / WebFetch / 改源码应被拒；模型只能 Read/Grep/Glob + 受限 Bash + WebSearch
- `fail` 任务出误报报告、不打靶场
- 描述与仓库无关的任务 **一轮** 静态后进入待复核；步骤条看得到 `gate_reason`；host_workdir 仍在
- runtime-dependent 的 SQL 注入走 `pass` + `runtime_dependent=true` 进入 reproduce，不得被收成 `needs_review`
- 待复核期间 Lab 可按 TTL 到期；默认重试从 source 整条重跑，也可从 audit 起重跑
