# 平台对象存储契约（MinIO）

> 版本: v1.1 · 2026-08-18
> 状态: 已确认
> 定位: 平台与 MinIO 的唯一对象契约。所有文件类持久化（源码、配方、证据、报告、节点运行包、未来头像）共用桶分层、key 语法和唯一客户端。
> 覆盖并收编：`2026-08-18-node-failure-corpus-design.md`（失败语料改为 kind=`node_run`，不再单开桶）。
> 阅读对象: 所有会读写对象的 Context / worker / 部署。

---

## 0. 已确认决策

| 项 | 决定 |
|---|---|
| 物理桶 | **3 个**，按寿命与访问分，不按功能堆桶 |
| 桶初始化 | 新部署由 `createbuckets` 一次建出**现行 3 桶**。它不是对象网关，只是 MinIO 的桶清单。Python `ensure_buckets()` 只对这 3 个再幂等兜底，禁止在业务代码里为新 kind 懒建第 4 个桶 |
| Key | `{kind}/{owner_id}/{...}` |
| 客户端 | **唯一**：`app/shared/object_store.py`。Context 禁止 `Minio(...)` |
| 新种类 | 只加注册表里的 `kind`，不开新桶 |
| 旧对象 | **开发阶段干净切换**：不做搬迁、**不做双读**。旧 4 桶名退出代码与 `createbuckets`。本地残留对象 / 指向旧桶的库行视为未命中（源码重 clone、配方重跑 AI、证据旧链接失效），可以删 |
| 新写入 | 一律走 3 桶 + 注册表 key |
| 磁盘 | `host_workdir` / `labs/{id}` 只是暂存或运行态，不是对象真相 |
| 禁止入桶 | `.secrets/`、LLM 凭据、明文密码 |
| 头像 | v1 **只注册** `avatar` kind，不上传 API |
| 失败语料 | kind=`node_run`，对象在 `crucible-task`；分析器不进 v1 |
| 新 API | v1 无对象管理 HTTP。证据预签名仍走现有 report 接口 |
| Mock | SDK 关闭时不写 `node_run`；其它对象路径保持各自 Mock 语义 |

明确不做：ELK、全量迁移旧桶、分析器自动改 skill、把 docker daemon / Celery stdout 当对象、公开列出任意前缀、跨用户读私有对象。

---

## 1. 问题

今天每加一类文件就多一个桶、一套 `Minio()`：

| 对象 | 桶 | Key | 客户端 |
|---|---|---|---|
| 源码 | `crucible-source` | `source/{host}/{project}/{sha}.tar.gz`（无 owner） | `project/source_cache.py` |
| 配方 | `crucible-lab-recipe` | `recipe/{owner}/{project}/{sha}.tar.gz` | `lab/recipe_store.py`（不在 createbuckets） |
| 证据 | `crucible-evidence` | `{task_id}/{uuid}/{filename}`（无 owner） | `report/storage.py` |
| 报告 JSON | `crucible-artifacts` | `reports/{task_id}/{id}.json` | 同上 |
| SDK jsonl | 无 | `host_workdir/.claude/**` | 无 |
| 头像 | 无 | 无 | 无 |

agent 直接 import `report.storage`，跨 Context。失败节点没有可查询对象。再加失败包/头像会变成第 5、第 6 套。

---

## 2. 三桶

| 桶 | 寿命 / 访问 | 用途 |
|---|---|---|
| `crucible-durable` | 长期、私有 | 内容寻址、可跨任务复用：源码、成功配方 |
| `crucible-task` | 任务域、私有；后续可对前缀做 lifecycle | 证据、报告文件、节点运行包（含 jsonl） |
| `crucible-public` | 长期；预签名或公开读 | 头像等用户资源 |

旧桶名 `crucible-source` / `crucible-evidence` / `crucible-artifacts` / `crucible-lab-recipe` **退出代码**。本地 MinIO 卷里若还在，不参与读写；开发环境可以直接清掉。

### 2.1 `createbuckets` 做什么、要不要规范

现网这个服务只干一件事：MinIO healthy 之后用 `mc mb --ignore-existing` 把**当时平台会写入的桶**建出来，然后退出（`restart: no`）。不配 lifecycle、不加 IAM、不代理上传。配方桶当时没写进脚本，于是 `recipe_store` 自己 `make_bucket`——这是清单漂移，不是 createbuckets 能力不够。

**要规范，而且只规范这一件事：** 新机器 `docker compose up` 之后、第一条业务请求之前，现行写入桶必须已经存在。清单 = 注册表里的 3 个物理桶，不再按 kind 建桶。

入口只保留：

```
mc mb --ignore-existing local/crucible-durable
mc mb --ignore-existing local/crucible-task
mc mb --ignore-existing local/crucible-public
```

`mc alias` 仍指向 `http://minio:9000`，账号与现网一致。实施时**删掉**对 `crucible-artifacts` / `evidence` / `source` 的 `mb`。

| 场景 | 行为 |
|---|---|
| 全新部署 | 只有这 3 个桶 |
| 已有开发 MinIO 卷 | `ignore-existing` 补上 3 个新桶；旧桶里的对象**不读**。需要干净环境时停栈清卷（需用户确认 `docker compose down -v`） |
| 业务再加 kind | 只改注册表的 key 前缀；createbuckets **不**加第 4 行 `mc mb` |
| 只起 API、没跑 compose | `object_store.ensure_buckets()` 对**同一 3 个名字**幂等；禁止在 Context 里 `make_bucket` |

测试用 Memory store，不跑这个容器。禁止为 `node_run` / `avatar` 再加功能桶。

---

## 3. Kind 注册表

唯一表（代码常量，禁止运行时动态注册未列 kind）：

| kind | 桶 | Key 模板 | 预签名 | v1 写入 |
|---|---|---|---|---|
| `source` | durable | `source/{owner_id}/{git_host}/{project_key}/{sha}.tar.gz` | 否 | 是（新缓存） |
| `recipe` | durable | `recipe/{owner_id}/{project_id}/{sha}.tar.gz` | 否 | 是（新配方） |
| `evidence` | task | `evidence/{owner_id}/{task_id}/{evidence_id}/{file_name}` | 是 | 是（新证据；`evidence_id`=`evidences.id`） |
| `report` | task | `report/{owner_id}/{task_id}/{report_id}/body.json` | 是 | 是（新报告文件） |
| `node_run` | task | `node_run/{owner_id}/{task_id}/{run_id}/{node_key}.tar.gz` | 否 | 是（节点 failed） |
| `avatar` | public | `avatar/{owner_id}/profile` | 是 | **否**（仅占位） |

`file_name` / `git_host` 入 key 前必须做路径安全：去掉 `/` `..`，限制字符。`owner_id` 与当前用户或任务所有者一致，禁止用「平台公共」空 owner 写私有 kind。

黑名单：不存在 kind `secret` / `credential` / `env`。测试断言 put 这些名字直接拒绝。

---

## 4. 唯一客户端

路径：`backend/app/shared/object_store.py`。

能力：`put(kind, owner_id, key_parts, data, *, content_type)`、`get`、`presign`、`delete`、`exists`。返回 `ObjectRef(kind, bucket, key)`。测试用 `MemoryObjectStore`。

规则：

- 只有注册表里的 kind 能 put。
- `presign` 仅当注册表允许；`source` / `recipe` / `node_run` 默认不预签名（worker 内 get）。
- Context **只**通过本模块访问对象。打包/解包仍在领域内（`pack_project_dir`、`pack_recipe`、打 `node_run` tar）。
- `node_run` 的 put + 写 `node_run_failures` 由 **task Service** 一次完成；agent 只调 Service，不自己拼 bucket。
- agent **禁止** `from app.contexts.report import storage`。归档证据改为 `ReportService.attach_evidence`（或 task 调 report Service）。
- `report/storage.py`、`source_cache.py`、`recipe_store.py` 的 `Minio()` 在切换后删除；领域文件只留 pack + 调 object_store。

---

## 5. 读路径（干净切换）

全部读写只走注册表里的桶 + key。v1 **不做**搬迁、**不做**旧桶双读。

**库里存了 bucket + object_key 的行**（`source_artifacts`、`evidences`）：下载/预签名用存下来的值。指向已退出的旧桶名或旧 key 形状 → 视为未命中（源码重新 clone 并覆盖该行；证据预签名失败不阻塞列表）。新写入把 `crucible-durable` / `crucible-task` 和新 key 写回该行。

**纯函数算 key 的路径**（配方）：只读 `crucible-durable` + `recipe/{owner_id}/{project_id}/{sha}.tar.gz`。未命中进现有 AI 循环。

源码新 key 含 `{owner_id}`：`source/{owner_id}/{git_host}/{project_key}/{sha}.tar.gz`。

---

## 6. 磁盘 vs 对象

| 位置 | 角色 |
|---|---|
| `{workdir_base}/audit-{task_id}` | 任务暂存（clone、runner、jsonl、`.vuln-env` 草稿） |
| `{workdir_base}/labs/{lab_id}` | 靶场运行态；重建来源是 kind=`recipe` |
| `host_workdir/.secrets/` | 仅内存盘/任务目录，**永不 put** |
| worker / API 进程日志 | 运维 syslog，不进 MinIO |
| MinIO | 跨机器、可查询的真相 |

成功任务仍 `rmtree` host_workdir。失败/取消/待复核仍可短期保留目录给人看；`node_run` 包才是失败样本真相。

---

## 7. kind=`node_run`（失败语料）

分析单元仍是一次 `NodeRun`。`env_ready` 多轮是同一对象里的 `attempts[]`。成功节点 v1 不上传。Mock 不上传。上传失败不阻断节点终态。

### 7.1 包内布局

```
manifest.json
attempts/
  N/
    previous_error.txt
    platform_error.txt
    submit.json
    session.jsonl          # 本轮 .claude/**/*.jsonl；可缺
    .vuln-env/             # 仅 env_ready
```

`manifest.json` 含 `schema_version: 1`、task/run/node_run/node_key、owner、project、commit_sha、profile 摘要、最终 `error_class` / `failed_stage` / `attempt_count`、每轮 stage 列表。源码不进包。

单轮 jsonl > 5MB 截尾，manifest 标 `session_truncated: true`。打包跳过 `.secrets/`，并对 `ANTHROPIC_*` / `sk-` 掩码。

### 7.2 索引

表 `node_run_failures`（task Context）：`owner_id, task_id, run_id, node_run_id, node_key, error_class, failed_stage, language, attempt_count, bundle_key`。唯一 `(run_id, node_key)`。v1 无 HTTP。删任务时删索引行，**不强制删** MinIO 对象。

`bundle_key` 存 object_store 算出的 key；`bucket` 可由 kind 推导，若要防注册表变更可冗余一列 `bucket`。

### 7.3 错误类

平台从 `failed_stage` + 根因摘要归类，模型不填：

`recipe_validation` · `port_conflict` · `compose_up.copy` · `compose_up.transfer` · `compose_up.build` · `compose_up.runtime` · `compose_up.policy` · `health_check` · `runner.no_submit` · `runner.timeout` · `docker.unavailable` · `unknown`

### 7.4 采集

暂存 `{host_workdir}/.node-failure/{node_key}/`。每轮 AI 结束立刻快照，避免下一轮覆盖 `.vuln-env`。节点最终 `failed` 再 put `node_run` + 写索引。`needs_review` / `cancelled` v1 不打包。`source` 失败可只有 manifest + `platform_error.txt`。

分析器（非 v1）：按索引捞 `ObjectRef`，只读建议，禁止直接改仓库。

---

## 8. 其它 kind 的写入时机（v1）

| kind | 何时 put | 失败策略 |
|---|---|---|
| `source` | 节点 0 clone 成功、写 `source_artifacts` | 与现网相同：缓存失败则下次再 clone |
| `recipe` | env_ready 探活成功、`mark_ready` 前 | 与现网相同：靶场已 ready，上传失败只打日志 |
| `evidence` | 用户上传或 worker 归档截图/产物 | 单文件失败跳过，不阻断报告 |
| `report` | 报告落库后写 body | 失败不阻断报告行 |
| `node_run` | 节点 failed | 不阻断终态 |
| `avatar` | 无 | — |

预签名仍只给证据/报告/未来头像。过期默认 3600s，与现网一致。

---

## 9. 测试

对象契约：

1. 注册表：3 桶名、6 个 kind、黑名单 kind put 失败。
2. `createbuckets` 入口**只**含 `crucible-durable` / `crucible-task` / `crucible-public`，不含 `crucible-node-failure`，也不再 `mb` 旧功能桶。
3. key 生成：含 owner；`../` 与 `/` 在 file_name 中被拒绝或剥掉。
4. Memory store：put/get 往返；未知 kind 失败。
5. 配方只读 `crucible-durable`；代码与测试常量不再出现 `crucible-lab-recipe`。
6. 证据新写入的 bucket 为 `crucible-task`，key 以 `evidence/{owner_id}/` 开头；预签名按该行存的 bucket/key。
7. agent 归档不再 import `report.storage`（架构测试或模块边界测试）。

`node_run`：

8. `classify_node_error` 表格驱动。
9. env_ready 两轮失败：`attempts/1` 配方不被第 2 轮覆盖丢掉。
10. 节点 failed 后 put 一次 + 索引有 `bundle_key`。
11. put 抛错：节点仍 failed，原 error_message 不变。
12. Mock / 成功节点：不 put。
13. 包内无 `.secrets/`。

---

## 10. 文档同步（实施时）

- 本文件为对象存储 SSOT。`2026-08-18-node-failure-corpus-design.md` 改为指向本文 §7。
- `infrastructure/docker-compose.yml` 的 `createbuckets` 按 §2.1 只建 3 个现行桶（实施时与 `object_store` 注册表一起改）。
- `docs/development-guide.md`：MinIO 改为 3 桶 + kind；createbuckets 只建这 3 个。
- `.claude/api-contract.md`：证据仍预签名；注明对象 key 新前缀；无新管理路由。
- `docs/agent-workflow.md`：失败节点有 `node_run` 包。
- 配方循环 spec 的 bucket 名改为 `crucible-durable`（与配方写入切换一起改）。
- 已删除 `report/storage.py`，客户端只留 `shared/object_store.py`。

不改编排出口、env_ready 5 轮、探活 90s。
