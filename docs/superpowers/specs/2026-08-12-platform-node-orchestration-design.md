# Crucible 平台多节点编排重塑设计

> 版本: v1.0 · 2026-08-12
> 状态: 设计稿(待评审 → 实施计划)
> 定位: 把 Agent 工作流从「插件单次大调用」重塑为「平台侧 6 节点编排」,每节点独立 chat,节点间用结构化 JSON 交接,以限制 AI 幻觉、支持断点续跑/单节点重试,并新增项目源码管理、结构化报告、任务重试/删除能力。
> 阅读对象: 接手后端编排链路 / 数据模型 / 前端进度的开发者。

---

## 0. 背景与动机

### 0.1 当前痛点

| 维度 | 现状 | 痛点 |
|---|---|---|
| Agent 编排 | 插件「一次大调用」(`agent-workflow.md` v0.2 决策),平台只翻译 `phase.updated` 事件 | 单 agent 长会话 → 幻觉难控;节点状态不可独立重试;`run_one.py` 只发 `start` 一个 phase,中间阶段无标准化机制 |
| 任务管理 | create / list / get / cancel | 缺 retry、缺 delete |
| 项目源码 | `project_address` 散落在 Task,每任务重新 clone、用完即删 | 无法一次 clone 多任务复用;无源码版本管理 |
| 报告 | `reasoning` 一整段 Text、`evidence_summary` 只存 count | 未结构化(无漏洞位置/PoC/利用条件/修复建议等字段);无 word 导出 |
| 凭据链路 | `build_env_from_provider` 不解密,容器拿到空 key | 当前所有任务必败(401),阻塞一切 |

### 0.2 核心决策

**架构方向(已与用户确认):平台多节点编排**

反转 `agent-workflow.md` v0.2 的「插件一次调用」决策,改为:Celery 编排器按节点序列调度,代码节点确定性执行、AI 节点独立 chat(独立 agent-runner 容器,context 物理隔离),节点间用结构化 JSON 交接。

**节点粒度(已与用户确认):6 节点**

| # | 节点 | node_key | 类型 |
|---|---|---|---|
| 0 | 源码获取 | `source` | ■代码 |
| 1 | 项目画像 | `profile` | ■代码(含 web 门禁 early-exit) |
| 2 | 靶场就绪 | `env_ready` | □AI 为主 + ■代码协作 |
| 3 | 白盒审计 | `audit` | □AI |
| 4 | 复现验证 | `reproduce` | □AI |
| 5 | 报告生成 | `report` | □AI + ■代码校验导出 |

**其他关键决策(已与用户确认):**
- 节点 2 协作模式:**AI 出配方(Dockerfile/compose)+ 代码执行 docker compose**(AI 不碰 docker.sock,守 `cap_drop ALL` 红线),排障循环 max 5 轮。
- AI 节点输出方式:**`submit_result` 工具**(强制 `tool_choice`),平台从 tool_call 取结构化 output_json,不靠解析文本。
- AI 节点用**独立 agent-runner 容器**(限幻觉的物理保证)。
- 插件改多 agent(领域知识 skills/references 零改动,只拆壳)。

---

## 1. 节点定义与交接契约

### 1.1 节点序列

```
0 源码获取 (■代码)
   │ source_path, commit, ref
   ▼
1 项目画像 (■代码)  ── is_web=false ──► 分支出口 A(非 web 结束,不下结论)
   │ {is_web, language, framework, port, has_dockerfile}
   ▼
2 靶场就绪 (□AI+■代码)  ── 5 轮排障失败 ──► 分支出口 C(基础设施错误,task failed,可 retry)
   │ {target_url, transport_shape, initial_creds, compose_path}
   ▼
3 白盒审计 (□AI)  ── gate_verdict=fail ──► 分支出口 B(误报,跳过节点 4)
   │ {kill_chain, defense_layers[], payloads[], gate_verdict}
   ▼
4 复现验证 (□AI)
   │ {reproduced, evidence[], screenshots[], verdict}
   ▼
5 报告生成 (□AI+■代码)
   │ report_data(8 节) + final_verdict
   ▼
 完成(归档 + 靶场清理)
```

### 1.2 分支出口

| 出口 | 触发节点 | 动作 | task 终态 |
|---|---|---|---|
| A 非 web | 节点 1 | node 2-5 标 `skipped` | `completed`,verdict 空(非 web 不下结论) |
| B 误报 | 节点 3 `gate_verdict=fail` | node 4 标 `skipped` | `completed`,verdict=`false_positive` |
| C 基础设施失败 | 节点 2 排障 5 轮失败 | run 标 `failed` | `failed`(可 retry) |

### 1.3 节点 input/output schema(结构化交接契约)

| 节点 | input_json | output_json |
|---|---|---|
| 0 source | `{git_url, ref}` | `{source_path, commit, ref}` |
| 1 profile | `{source_path}` | `{is_web: bool, language, framework, port: int, has_dockerfile: bool, detected_services[]}` |
| 2 env_ready | `{source_path, profile}` | `{target_url, transport_shape{protocol,listener,tls_termination,x_forwarded_proto,channel_check}, initial_creds{}, compose_path, started_containers[]}` |
| 3 audit | `{source_path, vulnerability_description, profile}` | `{kill_chain, defense_layers[{name,bypass}], payloads[], gate_verdict: pass\|fail, gate_reason}` |
| 4 reproduce | `{target_url, audit, transport_shape}` | `{reproduced: bool, evidence[{type,detail}], screenshots[], verdict: confirmed\|partial\|code_reachable\|code_smell\|false_positive\|not_reproduced}` |
| 5 report | `{全部前序 output}` | `{report_data{§1-§8}, final_verdict, cvss{vector,score,severity}}` |

### 1.4 判定档位(6 档,对齐插件)

废弃现有 `exists / not_exists / unconfirmed`,改为:

| verdict | 含义 | 对应插件档位 |
|---|---|---|
| `confirmed` | HTTP 可观察危害已复现 | 已确认 |
| `partial` | 仅支撑弱点/更窄影响 | 部分确认 |
| `code_reachable` | 仅代码可达,未 live 复现 | 代码可达 |
| `code_smell` | 最佳实践缺口无安全影响 | CODE SMELL |
| `false_positive` | Gate 推演不通/bypass 全失败 | 误报 |
| `not_reproduced` | 5 轮后运行时条件未解决 | 未复现 |

---

## 2. 数据模型

### 2.1 实体关系

```
users ─1:N─ projects ─1:N─ tasks ─1:N─ task_runs ─1:N─ node_runs
                                      │             │
                                      │             └─ input_json / output_json
                                      └────1:1──── reports ─1:N─ evidences

llm_providers(独立,后台管理)
```

核心变化:**新增 `projects` + `node_runs`**;`task_runs` 从「单 agent 会话」变为「6 节点编排容器」;`reports` 结构化。

### 2.2 Project(新增 context: `contexts/project/`)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | PK |
| name | String(255) | 项目名 |
| git_url | String(1024) | Git URL |
| default_ref | String(255) | 默认分支/tag |
| description | Text | 说明 |
| owner_id | UUID FK→users | 所有者 |
| detected_language | String(50) | 节点 1 画像后**回填**,后续任务复用省 AI |
| detected_framework | String(100) | 同上 |
| is_web | Bool | 同上 |
| last_cloned_at | DateTime | 源码缓存时间(P2 缓存复用用) |

P1 阶段:project 记录元数据 + 画像缓存,**不长期持有源码**(每任务按 run clone 到临时目录,结束清理)。P2 再做源码缓存复用。

### 2.3 Task(改造)

| 字段 | 变化 |
|---|---|
| ~~project_address~~ → **project_id**(UUID FK→projects) | 从散落 git_url 改为关联 project |
| project_ref | 保留(任务可指定不同分支,覆盖 project.default_ref) |
| vulnerability_description, priority, credential_refs | 不变 |
| status | `pending\|running\|completed\|failed\|cancelled\|needs_review` |
| **verdict**(新,String,索引) | 6 档(见 §1.4) |

### 2.4 TaskRun(语义变化)

从「一次 agent 会话」变为「一次 6 节点编排执行」。

| 字段 | 说明 |
|---|---|
| task_id FK, status | `pending\|running\|completed\|failed\|cancelled` |
| agent_session_id | 保留(取末节点 session,兼容查询) |
| **node_runs**(新关系) | 1:N → NodeRun |
| started_at, finished_at, error_message | 不变 |

### 2.5 NodeRun(新增 — 编排核心)

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | PK |
| run_id | UUID FK→task_runs | 所属编排 |
| task_id | UUID(索引) | 冗余,查询用 |
| node_index | Int | 0-5 |
| node_key | String(20) | `source\|profile\|env_ready\|audit\|reproduce\|report` |
| status | String(20) | `pending\|running\|completed\|failed\|skipped` |
| **input_json** | Text(JSON) | 节点输入(前序 output 组装) |
| **output_json** | Text(JSON) | 节点结构化产出(交接契约) |
| attempt | Int | 排障重试计数(节点 2 用,max 5) |
| agent_session_id | String(128) | AI 节点的 SDK session_id |
| error_message | Text | 失败诊断 |
| started_at, finished_at | DateTime | 耗时 |

索引:`(run_id, node_index)` 唯一、`(task_id, node_key)`。

### 2.6 Report(结构化改造)

| 字段 | 说明 |
|---|---|
| task_id, run_id, owner_id | 不变 |
| status | `draft\|generated\|published` |
| **verdict**(索引) | 6 档 |
| **cvss_score**(索引,Float) | 提到列,列表筛选/排序 |
| **severity**(String) | Critical/High/Medium/Low/None |
| **vulnerable_file**(String) | 定位用 |
| title, summary | 不变 |
| **report_data**(JSON Text) | 完整 8 节(见 §4.1) |
| md_artifact_key | MinIO:agent 产出的原始 md |
| docx_artifact_key | MinIO:导出的 docx |
| published_at | 不变 |

废弃 `reasoning`(整段 Text)、`evidence_summary`(仅 count)。reasoning 内容拆进 report_data。

### 2.7 状态机

```
Task:   pending → running → completed | failed | cancelled | needs_review
Run:    pending → running → completed | failed | cancelled
Node:   pending → running → completed | failed | skipped
Report: draft → generated → published | archived
```

- Task 状态由编排器在 run 结束时根据 node 状态汇总。
- Run failed(分支出口 C)→ Task 可 retry。
- Node skipped 仅由分支出口 A/B 触发,不允许编排器随意 skip。

---

## 3. 编排器与执行模型

### 3.1 编排器(改造 `contexts/agent/tasks.py`)

```
@celery_app.task(bind=True, name="agent.run_analysis")
def run_analysis(self, task_id, run_id):
    return asyncio.run(_run_analysis(task_id, run_id))

async def _run_analysis(task_id, run_id):
    for node_index in [0..5]:
        # 断点续跑:已 completed 的 NodeRun → 复用 output_json,跳过执行
        existing = load_node_run(run_id, node_index)
        if existing.status == "completed":
            context.update(jsonify(existing.output_json))
            continue

        node = create_node_run(run_id, node_index, input=build_input(node_index, context))

        if node_index in (0, 1):
            await run_code_node(node)        # worker 进程内函数
        else:
            await run_ai_node(node)          # 起 agent-runner 容器

        persist(node.status, node.output_json)

        # 分支出口
        if node_index == 1 and not context['profile']['is_web']:
            mark_skipped(run_id, [2,3,4,5]); finalize(task, verdict=None); break
        if node_index == 3 and context['audit']['gate_verdict'] == 'fail':
            mark_skipped(run_id, [4]); finalize(task, verdict='false_positive'); break
        if node_index == 2 and node.status == 'failed':
            fail_run(run_id); break          # 分支出口 C,task failed 可 retry

    if all_done_or_skipped:
        node5 report → validate report_data → render md → convert docx → archive
        finalize(task, verdict=report.final_verdict)

    cleanup_lab_containers()
```

### 3.2 执行单元

**代码节点(0/1)**:worker 进程内函数。

- 节点 0:复用现有 `git_clone_to_workdir`(`core/agent_runner.py`)。
- 节点 1:把 `plugins/.../references/project-detection.md` + `web-detection.md` 的规则表翻成 Python 规则引擎(plugin 调研确认是纯规则,预留了 `init_project.py` 入口)。产出 profile + web 门禁判定。

**AI 节点(2/3/4/5)**:每节点独立 agent-runner 容器。

- worker 写 `.node.json`(node_key + input_json + output_schema)到 host_workdir。
- 起 agent-runner 容器(bind mount host_workdir + 注入 ANTHROPIC_* env)。
- 容器内 `run_one.py` 加载对应子 agent,跑完调 `submit_result` 工具回传 output_json。
- worker 从 tool_call.input 取 output_json,**schema 校验后**落库。
- 失败/排障由 worker 控制循环:节点 2 的 max-5 轮(worker 起停靶场、收 `docker compose logs`、回喂 AI 改配置)。

### 3.3 节点 2 靶场协作循环

```
for attempt in 1..5:
    □AI: 读 source + profile + (attempt>1 ? 上轮 logs : ∅)
         → 写/改 .vuln-env/Dockerfile, .vuln-env/docker-compose.yml
    ■代码(worker): docker compose up -d --build
                    + curl {port} 健康检查
    if ready:
        取 target_url + transport_shape + initial_creds
        → output_json → node done
    else:
        收集 docker compose logs
        → 回 AI 重试(attempt+1)
if 5 轮失败:
    node failed → 分支出口 C
```

安全:AI 在 agent-runner 内只读写 `/workspace/.vuln-env/` 文本(容器可写区),**不碰 docker.sock**;docker compose 由 worker 在宿主/独立网络执行。守 `cap_drop ALL` + 只读 rootfs 红线。

### 3.4 AI 节点输出:`submit_result` 工具

每节点 agent 的 `allowed_tools` 末尾加 `submit_result`,SDK `tool_choice` 强制该工具:

```python
ClaudeAgentOptions(
    allowed_tools=["Read","Grep","Glob","Bash","Write","Edit","WebFetch","WebSearch","submit_result"],
    # submit_result 是平台注入的自定义工具,schema 由节点定义
)
```

`submit_result` 的 input schema = 该节点的 output_json schema(见 §1.3)。agent 必须调它才算完成;平台从 `tool_use_block.input` 取结构化数据,不解析文本。这是限幻觉的硬约束。

**注入机制**:`submit_result` 不是 SDK 内置工具,而是平台在容器侧的自定义工具。实现方式:在 `run_one.py` 通过 SDK 的 `mcp_servers` 或自定义工具回调注册一个名为 `submit_result` 的工具(其 input_schema 按节点从 §1.3 取),并在 `ClaudeAgentOptions` 用 `tool_choice` 强制。具体用 SDK 0.2.x 的哪种自定义工具 API(MCP server / `tools` 参数 / hook)在阶段 2 实施时验证选定——SDK 版本差异是已知风险点,需 PoC 确认。fallback:若自定义工具注入受阻,退回"prompt 要求 ```json 块 + 严格 schema 校验 + 失败重试"(可靠性降级,不阻塞主链路)。

### 3.5 节点 prompt 与事件

- **phase 事件问题自动消失**:平台知道节点边界,不再依赖 agent 发 `phase.updated`。前端节点进度由 `GET /tasks/{id}/runs/{rid}/nodes`(查 NodeRun 状态)+ SSE 推 `node.updated` 驱动。
- 现有 `agent_events` 表保留并**加 `node_run_id` 外键**(原 `run_id` 保留兼容),用于 AI 节点内的 tool.call / message 详情审计。前端主进度改用 NodeRun,点击节点展开看该节点的 agent_events。

---

## 4. 报告结构化

### 4.1 report_data JSON schema(8 节,对齐 `report_template.md`)

```yaml
report_data:
  # §1 产品介绍
  product_intro: ""

  # §2 漏洞描述
  vulnerability:
    type: "CWE-XXX: 名称"
    cvss:
      vector: "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H"
      base_score: 9.8
      severity: "Critical"
      scenarios: [{name, vector, score, condition}]
    vulnerable_file: "path/to/file"
    vulnerable_lines: "123-145"
    preconditions: ""
    entry_point: "URL"
    core_harm: ""
    environment_constraint: ""
    trigger_default: ""

  # §3 影响范围
  impact:
    affected_versions: ""
    unaffected_versions: ""
    trigger_condition_defaults: ""

  # §4 漏洞详情
  details:
    audit_analysis: [{file, lines, content, flaw_explanation}]
    poc_construction: {endpoint_choice_reason, bypass_methods[], payload_design, exploitation_chain}

  # §5 漏洞复现
  reproduction:
    transport_shape: {protocol, listener, tls_termination, x_forwarded_proto, channel_check}
    target_product: ""
    frontend_url: ""
    steps: [{step, action, observation, screenshot}]
    result_verification: [{item, result}]
    attack_chain_diagram: ""

  # §6 POC
  poc_commands: ["curl ..."]

  # §7 修复建议
  fix_suggestions: [{priority, suggestion, code_example}]

  # §8 报送判定
  reporting_decision: {recommendation, actual_harm, fix_priority, reason, risk_description}

# 横切
final_verdict: "confirmed|partial|code_reachable|code_smell|false_positive|not_reproduced"
```

### 4.2 报告生成与导出

- **节点 5 agent**:吃全部前序 output_json,按 `report_template.md` 8 节调 `submit_result` 回传 report_data。
- **校验**:复用 plugin `md_to_docx.py::_validate_report`(8 节齐全 / transport-shape 关键字 / 截图引用 / 截图存在)的等价校验逻辑,改成作用于 report_data JSON。
- **渲染 md**:report_data JSON → markdown(模板填空,生成人读友好的 §1-§8 md)。
- **转 docx**:复用 plugin `md_to_docx.py`(650 行,python-docx)。
- **存储**:report_data 落 `reports.report_data`;md/docx 上传 MinIO(`md_artifact_key` / `docx_artifact_key`)。
- **导出**:`GET /reports/{id}/export?format=docx|md` → 从 MinIO 取或按需生成。

---

## 5. 任务管理增强

### 5.1 重试

`POST /api/v1/tasks/{id}/retry` → 新建 TaskRun,**复用已成功 NodeRun.output_json**(断点续跑):

- 遍历上一个 run 的 NodeRun,`completed` 的节点把 output_json 拷进新 run 的 context,标记新 run 对应 NodeRun 为 `completed`(不重算)。
- 从第一个非 completed 节点起重跑。
- 节点 2 的 max-5 排障是**节点内** `attempt`,与 task 级 retry 不同层。

### 5.2 删除

- **软删**:`DELETE /api/v1/tasks/{id}` → `status=archived`,保留数据;级联清理活跃靶场容器。
- **硬删**:`DELETE /api/v1/tasks/{id}?hard=true` + 二次确认(header `X-Confirm: true`),物理删除 + 清 MinIO。
- 受保护状态:running 中的任务不可删(先 cancel)。

### 5.3 状态机对齐

Task `status` 增加 `archived`(软删)。verdict 6 档独立于 status(见 §1.4)。

---

## 6. 插件改造:单 agent → 多 agent

```
plugins/vuln-verify-expert/
  agents/
    env-builder.md   ← 节点 2(吃 run-project-env/references/*)
    auditor.md       ← 节点 3(吃 vuln-verify/SKILL.md Phase 1/2/2.5)
    reproducer.md    ← 节点 4(吃 vuln-verify Phase 3/4 + reproduction-guide.md)
    reporter.md      ← 节点 5(吃 report_template.md + Phase 5)
  skills/            ← 原样保留,作为各 agent 的知识库(system prompt 资产)
    run-project-env/references/
    vuln-verify/references/
```

- `vuln-verify-expert.md`(原总编排 agent)**退役**——阶段控制权移交平台编排器,领域知识拆进 4 个子 agent。
- skills/references **一行不改**,继续给子 agent 当 system prompt 资产。
- 每个 agent 的 `submit_result` 工具 schema 由平台按节点定义(§1.3)注入。

---

## 7. 前端

- **项目管理页**:project CRUD + 画像缓存展示。
- **任务详情步骤条**:6 节点状态(`source/profile/env_ready/audit/reproduce/report`),SSE 实时推 `node.updated`;点击节点展开 AI 节点内的 tool.call/message(从 agent_events)。
- **结构化报告页**:按 report_data 8 节渲染,导出 docx/md 按钮。
- `meta.ts::EVENT_PHASE_LABELS` 重命名为 `NODE_LABELS`,key 对齐 node_key。

---

## 8. 凭据链路修复(阶段 0,并行解锁)

### 8.1 当前 bug

`contexts/settings/service.py::build_env_from_provider`(working copy 未提交改动)直接把 `provider.api_key_encrypted` 当 API key 注入容器,未解密。DB 存的是 Fernet 密文(`gAAAAA...`),且当前 `AUTH_SECRET` 派生的 key 解不开(InvalidToken)→ 容器拿到空 key → DeepSeek 401 → 任务必败。

### 8.2 修复(凭据存储语义待选,见下)

`build_env_from_provider` 当前(working copy)直接把 `provider.api_key_encrypted` 当 API key 注入容器。无论选加密还是明文,核心是**存取必须一致**。

**凭据存储语义两个候选(阶段 0 实施时与用户最终确认):**

- **方案 A 恢复加密**(符合 `security.md §3` 红线):撤销 working copy 改动,`create/update` 用 `encrypt_secret`、`build_env_from_provider`/`test_connection`/`to_response` 用 `decrypt_secret`。
- **方案 B 彻底明文**(符合 working copy 当前意图):字段改名 `api_key`(去 `_encrypted` 误导),存取明文,响应层 mask;同时更新 `security.md §3` 放宽该红线。

两个方案的修复步骤:

1. 统一存取语义(按选定方案改 service/seed/credential_proxy 四个文件)。
2. 现有 DB 记录因存取不一致(密文 key 不匹配 / 明文错位)无法直接用 → 让用户在 web 重新输入真实 key,按新语义落库。
3. 次生 bug 修复:`executor.py:154` 读 stderr 时容器已在 `run_with_streaming` finally(`agent_runner.py:300`)里被删 → stderr_tail 永远空,错误消息只剩"非0退出"。修:在 `stop_and_remove` 前抓 stderr 存入 summary,或保留容器到结论落库后再删。

### 8.3 与架构重塑的关系

独立小修,不依赖新模型。可并行先做,解除阻塞让现有任务能跑。

---

## 9. 实施分阶段

每阶段独立 spec → plan → 实施 → 冒烟。

```
阶段 0(并行,解锁):凭据链路修复(§8)
阶段 1(领域地基):project context + node_runs + task/report 模型改造 + Alembic 迁移
阶段 2(编排核心):6 节点编排器 + submit_result 工具机制 + 代码节点(0/1)+ 插件多 agent 拆分
阶段 3(AI 节点):节点 2(靶场,AI+代码协作)+ 节点 3/4(审计/复现)
阶段 4(报告):节点 5 + report_data schema + md↔json 渲染 + docx 导出
阶段 5(任务管理):retry / delete / 状态机对齐
阶段 6(前端):项目管理 + 节点进度步骤条 + 结构化报告页
```

- 阶段 0 与阶段 1 可并行(凭据修复不依赖新模型)。
- 阶段 2 依赖阶段 1(编排器操作 node_runs)。
- 阶段 3/4 依赖阶段 2(节点定义)。
- 阶段 6 可在阶段 2 后逐步跟进(节点进度数据先有)。

---

## 10. 范围与非目标

### 10.1 本设计覆盖

- 6 节点编排架构 + 数据模型 + 状态机 + 分支出口
- project / node_runs / 结构化 report schema
- 任务 retry / delete
- AI 节点 submit_result 输出机制
- 节点 2 靶场协作循环(AI 出配方 + 代码执行)
- 报告 8 节 JSON + docx 导出
- 插件单 agent → 多 agent 拆分
- 凭据链路修复

### 10.2 非目标(后续)

- 浏览器 MCP 接入(独立子项目,见 `agent-workflow.md §8`)
- 源码缓存复用(P2,project 长期持有 clone)
- 多漏洞批量验证(当前一任务一漏洞)
- OIDC/RBAC(`development-guide.md` P1-8/9)
- DinD 生产部署(P2-15)

---

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 节点间结构化交接丢上下文(单 agent 连贯性) | input_json 组装前序全部相关 output;关键节点(3 审计)的 kill_chain 完整传递给 4/5 |
| 节点 2 排障循环 token 成本 | max 5 轮上限 + 每轮 logs 截断(前 2000 字) |
| submit_result schema 太严 agent 调不出 | schema 字段尽量宽容(可选字段);agent 调用失败 → 节点 failed → 可 retry |
| DB 迁移破坏现有数据(尤其 reports.reasoning) | 迁移脚本把旧 reasoning 转进 report_data.§4 或单独 legacy 字段;提供回滚 |
| 插件多 agent 拆分丢失原编排逻辑 | skills/references 不改;4 子 agent 的 prompt 由原 vuln-verify-expert.md 对应阶段拆出,review 确认无遗漏 |

---

## 附:关键决策溯源

| 决策 | 选项 | 选择 | 理由 |
|---|---|---|---|
| 编排方向 | 平台多节点 / 插件单调用 / 混合 | 平台多节点 | 用户诉求:细粒度限幻觉 + 节点状态可独立重试 |
| 节点粒度 | 10 节点 / 6 节点 / 超细多轮 | 6 节点 | 平衡限幻觉与编排复杂度 |
| 节点 2 协作 | AI+代码分离 / 挂 docker.sock / 独立特权容器 | AI 出配方+代码执行 | 守 cap_drop ALL 红线,AI 不碰宿主 docker |
| AI 输出 | submit_result 工具 / 文本 JSON 块 / 混合 | submit_result | 结构化原生,schema 内置,不靠解析 |
| 节点 1 性质 | 代码 web 门禁 / 并入 AI | 代码 | 纯规则可代码化,early-exit 省 AI |
