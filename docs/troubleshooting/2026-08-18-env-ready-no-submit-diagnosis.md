# env_ready 节点失败诊断：runner.no_submit + exit=137

> 2026-08-18 诊断记录。对象：任务 `5da090fc-11d3-4c81-9beb-72412bcfaf7d`
> （`microservices-feign-client`，Java Spring Cloud 三模块：Eureka-Server / Producer / Consumer）。
> 结论：两个独立根因叠加——**配方落位契约矛盾**（4 轮 compose up 结构性失败）+
> **孤儿巡检按 run 总年龄误杀**（attempt 5 容器被 SIGKILL，exit=137，又被误分类为 `runner.no_submit`）。

## 1. 故障概述

- 失败运行：run `3977ce30-501e-4540-957c-4e6b6c10e033`（09:36–10:10 UTC，墙钟约 2047s）
- NodeRun：`ceae8dda-d258-4513-9bb1-b9b3a9dcded1`，`node_key=env_ready`，status=failed
- 落库错误：

  ```text
  节点 env_ready 失败: Agent 没有提交节点结果就结束了
  原因: AI 节点 env_ready 未产出 .node_output.json (exit=137)
  下一步: 模型未调用 submit_result。检查该节点 prompt、MCP 工具是否注入成功，或重建 agent-runner 镜像。
  ```

- 失败语料索引：`node_run_failures` 一条，`error_class=runner.no_submit`、`language=Java`、
  `attempt_count=4`，快照包 `crucible-task` 桶 `node_run/b57e1671…/env_ready.tar.gz`
- 后续重试 run `0f1e61df-5414-4f18-9326-6f5bd6c3a1d6`（10:12 起）按同一时钟预计同样会在
  ~10:42 UTC 被巡检杀掉。

## 2. 记录在哪查

| 数据 | 位置 |
| --- | --- |
| 节点状态与错误 | `node_runs`（`node_key='env_ready'`，`error_message`、`attempt`、起止时间） |
| 失败分类索引 | `node_run_failures`（`error_class`、`failed_stage`、`bundle_key`） |
| 逐轮回喂与 AI 过程 | `agent_events`（按 `run_id` + `node_run_id`，看 `phase.updated` / `agent.thinking` / `agent.message`） |
| 失败快照（各轮 submit / previous_error / session.jsonl / .vuln-env） | MinIO `crucible-task` 桶，`bundle_key` 指向 tar.gz |

常用查询：

```sql
SELECT id, run_id, status, attempt, left(error_message,120), started_at, finished_at
FROM node_runs WHERE node_key='env_ready' ORDER BY created_at DESC;

SELECT sequence, event_type, left(payload,300) FROM agent_events
WHERE run_id='<run_id>' AND node_run_id='<node_run_id>' ORDER BY sequence DESC;
```

## 3. 时间线（UTC）

| 轮次 | 时间 | 失败点 | 回喂错误 |
| --- | --- | --- | --- |
| 1 | 09:36–09:42 | compose_up | 根级 `context: ..` 下 `COPY Eureka-Server/… not found` |
| 2 | 09:42–09:53 | compose_up | 每模块 `context: ../X` → `labs/{id}/Eureka-Server not found`；AI 写进模块目录的 Dockerfile 同步时被丢弃 |
| 3 | 09:53–10:04 | compose_up | 改 `./X` → 被解析成 `.vuln-env/X` not found |
| 4 | 10:04–10:05 | compose_up | 改回 `../X` → 仍然 `labs/{id}/X not found` |
| 5 | 10:05–10:10 | 容器被杀 | AI 已定位「平台把 .vuln-env 落到 lab 根执行」并重写配方，验证途中容器 exit=137 |

## 4. 根因 A：配方落位契约矛盾

skill 与平台暂存行为互相矛盾：

- `infrastructure/agent-runner/node-skills/env_ready/SKILL.md`（「配方形状」节）要求
  **`build.context` 指向原仓库模块**（如 `../Eureka-Server`），并**禁止把源码复制进 `.vuln-env/`**。
- 但平台执行前只把 `.vuln-env` 单独拷进 lab 目录，仓库源码并不在旁边：

  ```python
  # backend/app/contexts/agent/nodes/env_ready.py :: sync_recipe_to_lab
  src = Path(src_repo_dir) / ".vuln-env"
  dst = Path(lab_workdir) / ".vuln-env"
  shutil.copytree(src, dst, dirs_exist_ok=True)
  ```

于是任务 workspace 里合法的 `../Eureka-Server`，落到 lab（`labs/{id}/.vuln-env/`）后解析为
`labs/{id}/Eureka-Server`——必然 not found。AI 写在各模块目录里的 Dockerfile 也不会被拷走。
**多模块项目在当前契约下结构性无解**：AI 的 5 轮排障只是在 `./X` / `../X` / 根 context 之间打转，
每轮都「对症」但每轮都死在同一个暂存行为上。

## 5. 根因 B：巡检超时误杀 + 错误分类

attempt 5 只跑了约 5 分钟就 exit=137（SIGKILL），排除法：

- **非单容器超时**：每个 AI 轮次是独立容器、独立 1800s 定时器
  （`backend/app/core/agent_runner.py` `run_with_streaming`），该容器只存活约 317s。
- **非 Celery hard limit**：worker 存活并正常把节点落为 failed（Windows solo pool 不强制）。
- **最吻合**：run 总墙钟 09:36→10:10 = 2047s，超过 `agent_runner_timeout_seconds=1800`。
  孤儿巡检每 5 分钟扫一次，凡 `run.started_at` 年龄超时的 running 任务直接拆容器：

  ```python
  # backend/app/contexts/agent/runtime_cleanup.py :: should_keep_runtime
  def should_keep_runtime(status, age_seconds, timeout_seconds):
      """仅保留未超时的 pending/queued/running。"""
      return bool(status) and status in LIVE_STATUSES and age_seconds < timeout_seconds
  ```

  10:06:26 越过 1800s 门槛，下一个巡检周期（~10:10:33）拆掉容器——与失败时刻严丝合缝。

env_ready 节点「5 轮 AI 分析 + Maven 构建 + 探活（约 90s/轮）」对 Java 多模块项目天然超 30 分钟预算。

误分类链：容器被杀 → 无 `.node_output.json` → `node_failure.py::classify_node_error` 按关键词
「未产出」命中 `runner.no_submit`（`runner.timeout` 分支在它之前永远轮不到），掩盖了真实超时语义。

## 6. 可观测性缺口

- `node_runs.attempt` 全程停在 1，不随 5 轮排障递增，无法从表上看出用了几轮。
- exit=137 无原因标注（超时 / OOM / 巡检杀三者不可区分），排查只能靠对时间线。
- 巡检拆容器只有 worker 日志，不回写 NodeRun / agent_events。

## 7. 修复决策（2026-08-18 已定，当日实施）

1. **根因 A：选「compose 留在任务 workspace 就地执行、lab 只做登记」**。
   - `.vuln-env` 留在 `{host_workdir}/{repo}/.vuln-env`，AI 写的 `build.context: ../X`
     天然解析到仓库内模块；删除 `sync_recipe_to_lab` 文件暂存层（lab 目录不再拷配方）。
   - compose 用 `-p crucible-lab-{id}` 项目名隔离，Lab 状态机 / 复用 / stop / start /
     destroy / list_containers 全按 project 名操作，与文件位置无关。
   - `validate_compose_file` 安全校验的根从 lab workdir 改为任务 `host_workdir`，
     build context / bind mount 仍圈死在任务工作区内，边界不放松。
   - MinIO 缓存配方（`_try_cached_recipe`）解压落位到 `{repo}/.vuln-env`；`upload_recipe`
     上传源同步改为 workspace 的 `.vuln-env`。
   - `rebuild_lab`：按 projects 表 git_url shallow clone 源码到 `lab.workdir/{repo}` +
     MinIO 拉回配方 + `up --build`，与创建路径同构（重建=重新构建镜像）。
   - **放弃备选**：lab 暂存整个仓库（双份源码、拷贝耗时）；改 skill 允许源码进
     `.vuln-env`（污染仓库树、配方包膨胀）。同日评估过「docker-ops MCP 工具化」方案
     （AI 在容器内经文件队列驱动 compose up/logs 自迭代），因当前失败 100% 源于落位
     缺陷、未被证实需要 harness 化反馈回路，按 YAGNI 留作后续语料驱动选项。
2. **根因 B**：
   - 巡检强杀门槛与单容器超时（1800s）解耦：live run 需超过宽松硬顶
     （`agent_run_hard_timeout_seconds`，默认 7200s）才拆；单容器 1800s 定时器
     （`run_with_streaming` 内已有）仍是逐轮权威超时。强杀时写明原因并回写 DB。
   - 分类顺序调整：`exit=137`（非 OOM）判定提到「未产出」之前，归 `runner.killed`，
     不再误报 `runner.no_submit`。
3. **顺带**：`node_runs.attempt` 随 env_ready 排障轮次递增；exit=137 事件带原因标注。

## 8. 复盘数据指针

- 快照包：`node_run/b57e1671-8bb3-4b83-9919-dc3a7d8cc1df/5da090fc-…/3977ce30-…/env_ready.tar.gz`
  （含每轮 `previous_error.txt`、`platform_error.txt`、`submit.json`、`session.jsonl`、`.vuln-env/`）。
- 关键事件序列：run `3977ce30` sequence 91（架构分析）、133/269/369/426（各轮回喂）、
  456（attempt 5 定位暂存行为）、463（node.updated failed）。

## 9. 追加：同任务 audit 节点 runner.no_submit（2026-08-19 修复）

env_ready 修复后同任务重跑，source/profile/env_ready 全绿，audit 却以
exit=1 + `未产出 .node_output.json` 失败。DB 事件 sequence 246 的 thinking
给出铁证：模型自述 *"Now I need to call submit_result, but it's not in my
available tools"*——白盒审计本体已成功（gate_verdict=pass 完整走链），死在
收尾提交。

**根因 C（新）**：audit 的 submit_result 工具 schema 是五个节点里唯一带顶层
`allOf`（内含 `if/then/const` 条件分支）的。Anthropic 工具接口只保证
`type/properties/required` 子集，第三方网关（本例 360AI，`deepseek-v4-pro`）
解析不了顶层组合器时**静默丢弃整个工具定义**而非报错——SDK 侧 `tools/list`
正常、CLI 拿到的工具列表里没有 submit_result，模型自然调不了。同 run 内
profile（简单 schema）/ env_ready（properties 内嵌 anyOf）都成功，交叉验证
了「顶层组合器」这一唯一变量。

**修复（2026-08-19）**：

1. `runner/node_schemas.py` 删掉 audit 顶层 `allOf`，条件形状（pass 需
   kill_chain/payloads/runtime_dependent/core_claim；fail 需 kill_chain/
   defense_layers）改由顶层 `description` 文本传达；强校验本就只在后端
   `_validate_audit_output`，无校验损失。
2. 防护测试 `test_submit_schema_uses_only_anthropic_tool_subset`：禁止任何
   节点 schema 顶层再出现 `allOf/anyOf/oneOf/if/then/const` 等组合器。
3. 观测性补强（本次排障被两类信息黑洞拖慢）：
   - `ai_runner.py` 失败 detail 从「截头 300 字符」改「取尾 600 字符」——
     JSONL 末尾才是 agent.failed 真实死因，头 300 恰好全是 init/thinking；
   - `run_one.py` SystemMessage 过滤白名单加入 `mcp_server_error`/
     `stream_error`，MCP 层错误不再被当心跳静默吞掉。

**教训**：`agent.warning`（MCP 注入失败降级）走的是 run_one 自己的 print，
但 CLI 网关丢工具发生在 tools/list 之后、SDK 无异常路径可 hook——只有
放开 SystemMessage 过滤 + 尾部 detail 才能让这类「静默失败」现形。
