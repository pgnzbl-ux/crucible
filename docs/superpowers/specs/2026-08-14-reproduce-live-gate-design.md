# 节点 4 在线复现闸门与报告落库设计

> 版本: v1.1 · 2026-08-14
> 状态: 已评审
> 定位: 把 reproduce 收成「Agent 对已就绪靶场发 HTTP、平台只校形状并拒 docker」；成功路径的 8 节报告在同一会话末尾交齐，节点 5 只校验落库。一次 `run_ai_node`（reproduce）；不机械证伪危害；无 Chromium。
> 覆盖并修正：`2026-08-12-platform-node-orchestration-design.md` §1.1 节点 5 类型、§1.3 reproduce/report 产出、§4.1 `report_data` 嵌套 JSON；`run_one.py` 对 reproduce 仍允许 `docker`；`reproducer.md` 未声明平台无浏览器、禁止改靶场容器；`reporter.md` 假定成功路径再开一轮撰稿。
> v1.1：吸收平台化评审——用户交付物是同一份「报告」资源，不是第二个撰稿 Agent；8 节改为 Markdown 字符串；成功路径零第二轮 token；audit `fail` 仍走节点 5 出同构误报报告。
> 阅读对象: 编排 / agent-runner 工具策略 / reproducer / reporter / 报告页 / vuln-verify Phase 3–5。

---

## 0. 已确认决策

| 项 | 决定 |
|---|---|
| 节点 4 职责 | 在线复现：对节点 2 已拉起的靶场发 HTTP，确认 audit 的核心主张。成功路径同时完成 Phase 3–5（证据 + 8 节 Markdown） |
| 5 次变体 | **Agent 容器内自己做**。平台只跑一次 `run_ai_node`，不仿 env_ready 循环，不读 `attempt_count` |
| HTTP 谁发 | **只让 Agent 发**。平台注入改写后的 `target_url`，允许 `curl`/`WebFetch`。平台不探活、不复验 payload、不根据响应改 `verdict` |
| 平台 vs 模型 | 6 档 **不可机械证伪**。平台只校验产出**形状**；不判断 HTTP 是否真危害；不解析 Markdown 质量 |
| 形状（复现） | 见 §3.1。不合格 = 节点失败，不改写 `verdict` |
| 形状（报告） | 见 §3.2。8 节均为 **非空 Markdown 字符串**；嵌套 object/array 不合格 |
| 硬拦截 | reproduce Bash **拒 docker**（靶场已由平台 compose 启动）。`curl`/`Write`/`WebFetch` 仍开。不拦 `npm`/`pip`/`python urllib` |
| 浏览器 | **本轮不装 Chromium**。XSS/DOM 若只有 curl，不得 `confirmed`（应 `code_reachable`/`partial`）。插件写明 |
| 编排出口 | **不新增**。`fail` 仍 skip 本节点并出报告；`uncertain` 仍 skip 本节点且待复核。本节点的 6 档都进落库 |
| 报告正本 | `reports.report_data`：8 个 Markdown 字段的 JSON 对象。**不是**嵌套 8 节结构化树，也不是磁盘 `VULN-*/report.md` |
| 节点 5 成功路径 | **不调模型**。从 reproduce 的 `report_data` / `verdict` / `cvss` 拷贝，校验后作为本节点 output；`tasks.py` 仍负责插入 `reports` 行 |
| 节点 5 误报路径 | audit `fail`（无 reproduce）→ 仍 `run_ai_node` reporter，交 **同一套** 8 节 Markdown + `final_verdict=false_positive` |
| 索引列 | `verdict` / `cvss_score` / `severity` / `vulnerable_file` 是独立列，**不从 Markdown 里解析**。Agent 另交 `cvss` 与 `vulnerable_file` |
| Mock | SDK 关闭时 reproduce 交合法 `confirmed`（含 evidence + 8 节 Markdown + cvss）；fail 路径 mock reporter 交同构误报稿 |
| 过程产物 | reproduce 把截图写到 `{source_path}/VULN-*/img/`；成功任务由现有 `_archive_artifacts` 扫进 MinIO + Evidence，然后删 `host_workdir` |
| 前端报告页 | 8 节 Collapse + 每节 Markdown 解析。顶部判定条仍读 Report 索引列。本轮 **不**改步骤条 6 档摘要 |

明确不做：平台 5 轮循环、平台探活/复验 HTTP、截图落盘核对、Write 路径沙箱、给 runner 加 Chromium、改 6 档枚举、取消节点 5、fail 路径改纯模板（仍用短 AI）、对调节点顺序、拆 `vuln-verify` 目录、Markdown 内嵌图 URL 改写到 MinIO（后续）、双渲染旧嵌套 JSON。

---

## 1. 问题与目标

### 1.1 现状偏离

编排设计已是「节点 4 吃靶场 + audit，一次 HTTP 确认危害」。实际：

- schema 只强制 `verdict`，交 `{verdict: "confirmed"}` 就能过，没有 evidence、没有 `reproduced`。
- skill / `reproducer.md` 写「变体上限 5 次」，平台没有循环——这是对的（与 audit 同构），但文案没写清「不要等平台重开一轮」。
- `run_one.py` 对 reproduce 允许 `docker`。Agent 可能自己 compose，和「靶场由节点 2 拉起」冲突。
- skill 要求 XSS 必须真实浏览器截图；runner 镜像没有 Chromium。Agent 可能用 curl 反射就标 `confirmed`。
- T5 已让 reproducer 读 `runtime_dependent`；本设计不回退那一句。

报告侧额外偏离：

- 节点 5 再开独立 reporter 会话，把节点 4 的 JSON 当输入重写一遍 8 节——复现过程里已经形成的步骤/PoC/证据被丢掉再喂一次，烧第二轮 token。
- `report_data` 是深层嵌套 JSON；前端 `ReportContent` 用 Descriptions/Table 特判每个子对象。用户要的是「每节 Markdown 落库 + 网页解析」。
- skill 本地流程是 Phase 3 然后 Phase 5 **同一会话**；平台为了「HTTP 工具 vs 写公文隔离」拆成 4/5，隔离的收益小于成功路径的重复上下文。

### 1.2 目标闭环

```
reproduce 一次 run_ai_node（无节点内重试）
    │
    ├─ 形状合法（§3.1 + §3.2）
    │      → 节点 4 完成
    │      → 节点 5 代码：拷贝 report_data，不启 runner
    │      → reports 落库 → task=completed
    │
    └─ 形状不合法 / 无 target_url / 容器失败 → 节点失败 → task=failed

audit 出口 B（进入本节点之前）:
    skip reproduce
    → 节点 5 AI：同构 8 节误报 Markdown
    → reports 落库 → task=completed, verdict=false_positive

audit 出口 D:
    skip reproduce 和 report（无报告页）
```

步骤条仍是 6 格：成功路径「报告」瞬间完成（落库）；用户看到的交付物永远是同一张报告页。

### 1.3 为什么不取消节点 5、也不维持独立撰稿 Agent

| 选项 | 用户视角 | 平台管理 | 否决理由 |
|---|---|---|---|
| 成功后再开 reporter | 打完 HTTP 还要等一轮「写公文」 | 两套作者、两套上下文 | token + 延迟；复现细节二次转述失真 |
| 取消节点 5 | 成功有报告、误报没有步骤或没有报告 | 出口 B 与成功路径产物入口不一致 | 误报也必须有报告页；步骤条不能有时 5 格有时 6 格 |
| **成功路径 5=落库；fail 路径 5=短 AI** | 永远 6 步；有报告就同一页 | 一份 `reports` 资源、同一 schema | 本设计 |

节点步骤回答「沙箱干了什么」。报告是任务交付物。谁写 Markdown 是实现细节；`reports` 表才是 SSOT。

---

## 2. 与 vuln-verify skill 的映射

权威方法论仍是 `plugins/vuln-verify-expert/skills/vuln-verify/SKILL.md` Phase 3–5 与 `references/reproduction-guide.md`、`references/report_template.md`。本设计不改写证据纪律正文，只规定平台切分：

| skill | 平台（成功路径） | 平台（audit fail） |
|---|---|---|
| 一次 HTTP 测核心主张 | Agent 在 reproduce 容器内发；平台允许 curl/WebFetch | 不打靶 |
| 首测失败 → 变体上限 5 次 | Agent `maxTurns` 内自己做；平台不循环 | — |
| 诊断证据 ≠ 确认证据 | 文案约束；平台不解析响应体 | — |
| XSS 必须浏览器截图 | 平台无 Chromium → 不得 `confirmed` | — |
| Phase 4 清理 | Agent 用 HTTP 清理；禁止 docker | — |
| Phase 5 报告 | **reproduce 同一会话末尾**交 8 节 Markdown | reporter 短会话交同构 8 节（说明为何误报） |

不把 skill 拆成两份文件。平台注写清：Crucible 成功路径 Phase 5 不另开节点。

---

## 3. 产出契约

### 3.1 复现闸门（节点 4 必填）

`ai_runner` 的 reproduce schema 与 `submit_result` 工具定义同步。编排器不按 `verdict` 分支（一律进节点 5 落库，除非本节点失败）。

一律必填：`verdict`（6 档枚举）。

| verdict | `reproduced` | `evidence` | `screenshots` |
|---|---|---|---|
| `confirmed` | 必须 `true` | 至少 1 条；每条 `type`、`detail` 均为非空字符串 | 字符串数组，允许 `[]` |
| `partial` | 必须 `true` | 同 `confirmed` | 字符串数组，允许 `[]` |
| `false_positive` / `not_reproduced` | 必须 `false` | 不强制 | 字符串数组，允许 `[]` |
| `code_reachable` / `code_smell` | 不强制 | 不强制 | 若出现则必须是字符串数组 |

`screenshots` 缺省视为 `[]`。`evidence` 出现但不是数组 → 校验失败。

不新增 `attempt_count`。不读磁盘上的截图文件。

错误字符串必须能被测试 `in` 命中：

- `verdict 必须是 confirmed|partial|code_reachable|code_smell|false_positive|not_reproduced`
- `confirmed/partial 需要 reproduced=true`
- `false_positive/not_reproduced 需要 reproduced=false`
- `confirmed/partial 需要 evidence 至少 1 条`
- `evidence 条目需要非空 type 和 detail`
- `screenshots 必须是字符串数组`

### 3.2 报告正本（8 节 Markdown）

`report_data` 是 JSON **对象**，8 个键全部必填，值全部是 **非空字符串**（Markdown 正文）。禁止 object / array / null。

| 键 | 对应模板节 | 正文应覆盖（文案约束，平台不扫关键字） |
|---|---|---|
| `product_intro` | §1 产品介绍 | 一段话 |
| `vulnerability` | §2 漏洞描述 | 类型/CWE、CVSS 叙述、文件定位、前置条件、入口、危害、环境限制 |
| `impact` | §3 影响范围 | 受影响/不受影响版本、触发条件默认值 |
| `details` | §4 漏洞详情 | 代码审计（file:line + 原文 + 缺陷）+ PoC 构造思路 |
| `reproduction` | §5 漏洞复现 | 环境准备（含 transport-shape）、步骤、结果验证、攻击链。步骤里可用 `![alt](img/...)` |
| `poc_commands` | §6 POC | curl 等命令，用 fenced code |
| `fix_suggestions` | §7 修复建议 | 按优先级 + 必要时代码示例 |
| `reporting_decision` | §8 报送判定 | 建议/实际危害/优先级/理由/风险 |

**标题归平台**：字符串里 **不要**写 `## 1.` / `## 2.`……。可写 `###` 小节、表格、代码块、图片。导出与 Collapse 标签由平台加 `## N. 标题`。

平台校验（失败 = 节点失败，不改写内容）：

- 8 键齐全
- 每个值是 `str` 且 `strip()` 非空
- 值若是 dict/list → 失败（防止旧嵌套 JSON 混入）

错误字符串必须能被测试 `in` 命中：

- `report_data 需要 8 节 Markdown 字符串`
- `report_data.<key> 必须是非空字符串`（`<key>` 为缺的那一节）

成功路径：这 8 节挂在 **reproduce** 的 `submit_result` 上，与 §3.1 同一次提交。缺报告节 = 节点 4 失败（不要「复现过了报告没有」的半成品）。

误报路径：这 8 节挂在 **report** 的 `submit_result` 上。无 PoC/步骤时正文写明「未打靶 / 白盒闸门未通过」，仍须非空。

不兼容旧嵌套 `vulnerability: {type, cvss, ...}`。开发库不双渲染。

### 3.3 索引字段（不从 Markdown 解析）

列表筛选、详情页顶栏、导出元数据用独立字段，与 8 节正文分开：

| 字段 | 成功路径来源 | 误报路径来源 |
|---|---|---|
| `verdict` / `final_verdict` | reproduce.`verdict`；节点 5 原样拷贝为 `final_verdict`，禁止改写 | reporter.`final_verdict`，必须是 `false_positive` |
| `cvss` `{vector, base_score, severity}` | reproduce 必填（对象；`base_score` 为 number） | reporter 可交；缺省则 Report 列留空 |
| `vulnerable_file` | reproduce 字符串，允许 `""`（code_smell 等无定位） | reporter 字符串，允许 `""` |

`tasks.py` 插入 Report 时写入 `verdict`、`cvss_score`、`severity`、`vulnerable_file`、`summary`（`product_intro` 前 500 字）。

### 3.4 reproduce `submit_result` 总表

成功路径一次交齐：

```
verdict, reproduced, evidence, screenshots     ← §3.1
report_data.{8 keys}                           ← §3.2
cvss, vulnerable_file                          ← §3.3
```

report 节点（仅 fail 路径 AI）一次交：

```
report_data.{8 keys}
final_verdict = false_positive
cvss?, vulnerable_file?
```

---

## 4. ReproduceNode

编排器对 reproduce `execute` **一次**；节点内 **一次** `run_ai_node`。不仿 `EnvReadyNode` 做 attempt 循环。

保持现状：

- 缺 `env_ready.target_url` → `RuntimeError`（节点失败）
- `rewrite_url_for_agent_container`
- 整包前传 `audit`（含 `runtime_dependent`）
- 有 `lab_id` 时 `LabService.touch`

禁止为本节点加 `attempt` 参数或内部 for 循环。

`validate_output("reproduce")` 连续跑 §3.1 与 §3.2/§3.3。Mock 样例必须带 8 节 Markdown，否则 SDK 关闭联调会在节点 4 失败。

### 4.1 产物流

```
reproduce Agent
  ├─ submit_result → node_runs[reproduce].output_json
  │     verdict / evidence / screenshots / report_data(8 md) / cvss / vulnerable_file
  └─ Write 截图 → host_workdir/{仓库}/VULN-*/img/

report 节点
  ├─ 若 reproduce.output 含合法 report_data
  │     → 不启 runner；output = 拷贝 + authored_by=reproduce
  └─ 若 reproduce 被 skip（audit fail）
        → run_ai_node reporter → 同构 report_data + authored_by=reporter

tasks.py 收尾（落库逻辑保留，补索引列）
  ├─ 若有 report_data 且 status ∈ {completed}   ← needs_review 无报告，不改这条语义
  │     → INSERT reports（JSON 文本 = 8 节 Markdown 对象）
  │     → _archive_artifacts：扫 VULN-*/ 与 .vuln-env/ → MinIO + Evidence
  └─ should_retain_hostdir？保留目录 : rmtree
```

含义：

- skill 仍可在本地写 `report.md`；**平台正本是 8 节 Markdown 字段**。不强迫再写一份磁盘 md。
- `screenshots` 只是文件名。本轮 **不**核对文件是否在盘上；没写成文件的图不会进 MinIO。
- Markdown 里的 `![alt](img/...)` 本轮原样展示（相对路径在详情页可能裂图）。URL 改写到 Evidence/MinIO **本轮不做**。
- audit `uncertain` skip report → 没有 `report_data` → **不走归档**；靠保留 `host_workdir` 给人看（与出口 D 一致）。
- 成功任务归档后删除工作区：截图若要在详情页长期可见，只能靠 Evidence/MinIO，不能靠本地 `VULN-*`。

---

## 5. ReportNode（双模）

`node_key` 仍是 `report`，步骤条第 6 格不变。

```
execute:
  reproduce = previous_outputs.reproduce
  if reproduce 存在且含 report_data:
      校验 §3.2（失败 → 节点 5 失败；正常不应发生，节点 4 已校过）
      return {
        report_data,
        final_verdict: reproduce.verdict,
        cvss: reproduce.cvss,
        vulnerable_file: reproduce.vulnerable_file,
        authored_by: "reproduce",
      }
  else:
      # 仅出口 B：audit fail，reproduce skipped
      run_ai_node(reporter)
      校验 §3.2；final_verdict 必须 false_positive
      返回时加 authored_by: "reporter"
```

成功路径 **零** `run_ai_node`。测试断言：有 reproduce.report_data 时不 import/调用 runner。

`is_ai`：实现上允许恒 `True`（步骤条样式）或按输入分支；**行为**以是否调用 runner 为准。

Reporter 工具策略保持现状（本设计不收紧 report 的 docker/curl——fail 路径不该打靶；插件文案禁止 HTTP。硬拦截 fail 路径 curl **本轮不做**，避免和 reproduce 工具表缠在一起）。

---

## 6. 工具策略（`run_one.py`）

`REPRODUCE_DENY_RES`：仅 `docker`（`\bdocker\b`，覆盖 `docker compose` / `docker-compose`）。挂在 `_classify_bash` 的 `env_ready` / `audit` 分支旁：`node_key=="reproduce"` 时扫这份名单。

交叉锁定：

- reproduce：`curl` **allow**；`docker` **deny**
- audit：`curl` 仍 **deny**（本设计不回退 T3）
- env_ready：`docker`/`npm` 仍 **deny**

`_allowed_tools_for("reproduce")` 保持默认全集（含 Write/Edit/WebFetch）。不收 Write 路径。

`NODE_INPUT_SCHEMAS["reproduce"]` 与 worker 对齐：§3.1 + `report_data` object + `cvss` + `vulnerable_file`。`NODE_INPUT_SCHEMAS["report"]` 的 `report_data` 描述改为「8 节 Markdown 字符串」。真正拒形状仍是 worker `validate_output`。

---

## 7. 插件文案

### 7.1 `agents/reproducer.md`

在「输入」后插入「Crucible 平台」专章（口吻对齐 auditor / env-builder）：

- 做 Phase 3 / 4 / **5**。8 节报告在本节点 `submit_result.report_data` 交齐，**不要**等节点 report
- 靶场已由节点 2 拉起。禁止 `docker` / `compose`
- 必须对注入的 `target_url` 发 HTTP。平台不代打、不根据响应改档
- 无真实浏览器：XSS/DOM 若只有 curl，不得 `confirmed`
- `audit.runtime_dependent===true` 时 payloads 是候选（保留 T5 已有句）
- 5 次变体在本容器内完成，不要等平台重开一轮
- `report_data` 每节是 Markdown 正文，不要 `## 1.` 这种平台标题；不要嵌套 JSON

`submit_result` 字段表改为与 §3.4 一致。

开篇「已走通调用链并通过 Gate」改为兼容 `runtime_dependent`（T5 parked minor，本节点收口）。

### 7.2 `agents/reporter.md`

改为 **仅误报路径**：

- 只在 audit `gate_verdict=fail`、无 reproduce 时由平台拉起
- 禁止发 HTTP / 改靶场
- 交与 §3.2 同构的 8 节 Markdown；说明白盒为何不通；`final_verdict` 必须 `false_positive`
- 不再要求嵌套 JSON，不再写「供导出 Word」为完成条件

### 7.3 `skills/vuln-verify/SKILL.md`

在 Phase 3 / Phase 5 节加平台注（不要删 Phase 3–5 正文）：

- Crucible 成功路径：reproduce = Phase 3–5，一次会话
- 无 Chromium；docker 由平台管
- 平台正本是 8 节 Markdown 字段，不是 `report.md`

不改 `references/reproduction-guide.md` / `report_template.md` 全文（模板仍给人读；键名与节号对齐即可）。

---

## 8. 权威文档

实施时回写（本文件是本轮行为真相）：

- 编排设计 §1.1：节点 5 改为「■代码落库；仅出口 B 为 □AI」
- 编排设计 §1.3：reproduce 产出补 §3.1–§3.3；report 产出为同构 `report_data` + `authored_by`
- 编排设计 §4.1：嵌套 JSON schema **作废**，改为 8 个 Markdown 字符串；§4.2 节点 5 不再默认开 Agent
- `docs/agent-workflow.md`：回退环标明是 **Agent 内**循环；报告由 reproduce 交、节点 5 落库
- `docs/development-guide.md`：§3.1 补「reproduce 一次 AI、拒 docker、成功路径报告同会话」；不新开 P0
- `.claude/api-contract.md`：报告详情 `report_data` 8 节类型改为 string；导出 md = 平台加标题后拼接

不改 `2026-08-14-audit-static-gate-design.md` 出口表（B 仍跑 report，D 仍 skip report）。

---

## 9. 前端

**本轮要改** `ReportContent`：

- 8 节仍用 Collapse，label 为 `§N 标题`
- children：对 `report_data[key]` 做 Markdown 解析（与仓库现有 MD 组件对齐；没有则加一个最小渲染，代码块/表格/列表/链接）
- 顶栏判定 / CVSS / 漏洞文件：**只读** Report 的 `verdict` / `cvss_score` / `severity` / `vulnerable_file`，不再从嵌套 `vulnerability.cvss` 取
- `report_data` 为 null 时仍回退 `reasoning`
- 不双渲染旧嵌套对象（类型不是 string 的节显示「报告格式已升级，请重新跑任务」一类空态即可，不必写迁移器）

导出：

- `format=md`：`render_report_md` 改为给每节加 `## N. 标题` 再拼接正文（正文已是 MD，不再把 object 填进表格模板）
- `format=json`：仍返回 8 键对象（值是字符串）+ 索引字段

**本轮不改** `summarizeNodeOutput('reproduce')`（步骤条 6 档中文摘要仍留给后续）。

---

## 10. 测试范围

先红后绿。至少覆盖：

| 项 | 断言 |
|---|---|
| 复现形状拒绝 | confirmed 无 evidence；confirmed 且 `reproduced=false`；false_positive 且 `reproduced=true`；evidence 条目缺 detail；screenshots 为字符串 |
| 复现形状接受 | confirmed + 1 条 evidence + 8 节 md + cvss |
| 报告形状拒绝 | 缺一节；某节为 object；某节空字符串 |
| 报告形状接受 | 8 个非空字符串 |
| Bash | `docker compose up` 在 reproduce deny；`curl` 在 reproduce allow；`curl` 在 audit 仍 deny |
| ReproduceNode | `execute` 无 attempt 循环；缺 target_url 仍失败 |
| ReportNode | 有 reproduce.report_data → 不调 `run_ai_node`，output.authored_by=`reproduce`，final_verdict 等于 reproduce.verdict |
| ReportNode | 无 reproduce → 调 runner；mock/夹具 `final_verdict` 非 `false_positive` 则校验失败 |
| 渲染 | `render_report_md` 输出含 `## 1. 产品介绍` 且正文来自字符串，不访问 `.get("type")` |
| 插件 | `reproducer.md` 含「禁止 docker」「无真实浏览器」「Phase 5」；`reporter.md` 含「仅误报」 |
| 前端 | ReportContent 对 string 节走 Markdown（组件测试或现有 report 测试扩展） |

不测「第 2 轮容器才 confirmed」——没有平台循环。不测截图文件是否存在。不测 MD 图片是否能加载 MinIO。

---

## 11. 验收

- 真实 SDK 下，reproduce 容器 `docker` 被拒；`curl` 可发向 `host.docker.internal` 靶场
- `confirmed` 无 evidence、或无 8 节 Markdown 的 submit_result 被 worker 拒 → 节点 4 失败
- 成功路径 worker 日志 / 测试：节点 5 无第二只 agent-runner 容器
- XSS 任务若 Agent 只交 curl 反射，文案要求不得 `confirmed`（平台不机械抽查响应体）
- 详情「报告」页 8 节 Markdown 可读；导出 md 为人读的 8 个二级标题
- audit `fail` 仍有报告页（误报说明）；`uncertain` 仍无报告页
- audit `uncertain` / `fail` 出口不被本设计回退
