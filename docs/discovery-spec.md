# Crucible 代码审计与漏洞挖掘规格

> 状态：已确认  
> 版本：1.5  
> 业务定位：AI 辅助下的代码审计漏洞挖掘平台

## 1. 产品边界

### 1.1 平台主语

Crucible 的主流程是“项目资产 → 代码审计 → 漏洞线索 → 终认 → 漏洞报告”。
漏洞验证不是独立平台定位，而是代码审计中的终认能力；对用户已有明确线索时，平台同时提供次级的定向验证入口。

### 1.2 用户对象

- 项目资产（Project）：待审计的 Git 仓库或上传源码包。
- 代码审计（Task，`task_type=discovery`）：对一个确定源码版本执行发现侧流水线。
- 漏洞线索：扫描聚类后经二审裁剪，**仅合格可疑真洞**进入终认队；误报不作为用户可见对象长期占库。
- 终认记录（LeadRun 或派生 verify Task）：对单条合格线索执行白盒审计与可选动态复现。与 `task_type=verify` 共用同一套 AuditNode / ReproduceNode，不另起产品。
- **漏洞报告（产品入口）**：主导航「漏洞报告」。其下分两个 Tab，不得再把「审计报告」当作一级导航标题。
  - **审计报告 Tab**：按**代码审计任务**归档。列表项是一次 discovery 任务（项目 + 源码版本 + 任务摘要）；点进任务后，才展开该任务下终认成功的**一漏洞一份**报告（§11.1）。任务级聚合摘要（漏斗/范围）可作为任务页眉，不得替代单漏洞报告。
  - **验证报告 Tab**：用户下发的 `task_type=verify`；**一次验证任务一份报告**（与现网行为一致），不按多线索拆分。
- **单漏洞报告**：仅当 discovery 线索终认落地为 `confirmed` / `partial` / `code_reachable` 时生成（来源引擎无关）。未终认成功的线索不得冒充漏洞报告。
- 任务级聚合（Report，discovery）：一次代码审计的范围/漏斗/清单与对单漏洞报告的索引；不以是否确认漏洞为生成前提。

### 1.3 安全结论原则

- 未审不等于误报。预算未覆盖、`need_more_context`（二审未决）不得标成误报，也不得送终认。
- 工具失败、模型失败、预算耗尽或上下文不足不得静默归类为误报。
- 自动结论必须保留证据、输入摘要、模型/规则版本和可追溯运行记录。
- Agent 输出不可信：平台用 schema 拒收无证据、消毒有效仍报可疑真洞的 `submit_result`。
- 所有项目、任务、线索、终认与报告必须按 owner 隔离；无权对象统一按不存在处理。

### 1.4 术语（对用户禁止裸用 `tp` / `fp`）

代码内存的是英文缩写；页面、SSE 事件文案、报告必须用中文。

| 代码 | 英文 | 对用户文案 | 含义 |
|---|---|---|---|
| `fp` | false positive | **误报** | 二审认为不是漏洞 |
| `tp` | true positive | **可疑真洞** | 二审认为像真漏洞，**尚未终认** |
| `need_more_context` | — | **二审未决** | 切片上三问答不上，不是误报也不是真洞 |

终认另有一套词：`confirmed` / `partial` = 已确认；`false_positive` = 终认证伪；`code_reachable` = 无靶场时的代码可达。全程都在当前 15 节点编排里（含 `api_inventory` / `api_hunt`）。

验证方式对用户文案：`lab` = **靶场验证**；`code_path` = **代码闭环**（无靶场或未跑动态复现时的白盒终局）。

## 2. 发现质量规则

### 2.1 发现来源

四条并行线索：Semgrep、Gitleaks、OSV、API 猎洞（`engine=api_hunt`）。前三条经扫描节点独立执行、独立记账，汇入 `cluster`（**不含** `api_hunt` RawFinding）；猎洞经 `api_inventory → api_hunt` 自建 AlertGroup。单引擎/猎洞失败不得抹去其它线索。扫描复核链（`cluster → screen → triage`）与猎洞并列，互不阻塞；`dispatch` 等两端都 terminal 后统一按 §2.7 入队。

### 2.2 反锚定

AI 二审默认不接收静态引擎的最终结论措辞，只接收规则身份、定位、代码上下文和数据流证据。人工定向验证默认也不附带引擎结论，用户显式选择时除外。

### 2.3 HypothesisPack

每条进入 AI 工位或终认工位的线索必须是单一、封闭的假设包，至少包含 CWE、文件位置、函数、证据片段、可用数据流和待证明条件。禁止把多条无关线索拼入同一 prompt。

### 2.3.1 线索叙事（规范化人读字段）

不论引擎原文形态如何，凡进入 Adjudication（含 `api_hunt` 直出）的线索必须写入统一叙事字段，供线索详情与后续漏洞报告复用。引擎 RawFinding 原文保留，叙事是规范化层，不得覆盖命中片段。

| 字段 | 要求 |
|---|---|
| `summary` | 1～3 句漏洞简述（是什么、在哪、何害） |
| `reasoning` | 代码/依赖推理过程（入口→路径→危险点→消毒或可达结论；OSV 可用依赖调用与公告依据） |
| `why` / `evidence` | 保持现有短 bullet 与 `[{file, lines}]`；`tp` 时 evidence 非空 |

写入责任：

- **Semgrep / Gitleaks**：T3 `triage` 的 `submit_result` 对 `tp`（建议 `fp` 亦然）必填 `summary` + `reasoning`。
- **OSV**：默认 `bypass`，**不进**昂贵 T3；用确定性模板写入同一 `summary` / `reasoning`（依赖、编号、严重度、`called`、修复版本等）。仅 `called=true` 且后续 T3 认为可达才允许入终认队（§2.7）。
- **API 猎洞**：仍绕过 screen/triage，但直出 Adjudication 时必须带同一叙事字段。

二审输入仍遵守 §2.2 反锚定；叙事是**输出**给人类，不是把引擎结论当真理喂回模型。

### 2.4 聚类与二审

确定性代码降噪必须在 AI 二审之前完成。归一化阶段把引擎结构化信号写入 Finding 的 `raw`（不另建列）：

| 引擎 | 降噪可用信号 |
|---|---|
| Semgrep | `confidence`（HIGH/MEDIUM/LOW/UNKNOWN）、`category`、`has_dataflow`（与 codeFlows 一致） |
| Gitleaks | `rule_class`（known \| generic）、可选 `entropy` |
| OSV | `called`（bool\|null）、`unimportant`（若有）；依赖组件名/版本 |

原始发现经降噪后再按任务、CWE、文件、函数及重叠行区间聚类。AI 二审只接收等级 A/B 的假设包；输出 `tp | fp | need_more_context | parse_failed | bypass`、置信度、理由、证据、**叙事字段（§2.3.1）**和（可疑真洞时）合格门字段。二审**不是**终认：它只在切片上回答三问（来源与约束、消毒能否绕过、路径是否可达），禁止 HTTP、禁止全仓挖洞。

- **明确误报 `fp`**：不展示、不写（或立刻删）Postgres 线索组，只加漏斗计数。无复活入口。
- **二审未决 / 预算未审**：漏斗「未覆盖或未决」，不标误报、不进终认。
- **合格可疑真洞**：见 §2.7，才进入 Redis 终认队，交给 LeadWorker。

OSV 不是 SAST。不得把全部 GHSA 当白盒真洞；仅当本仓库**调用了**受影响组件 API（`called`）且 T3 认为可达，才允许入队；否则只进漏斗「依赖情报」计数，并用 §2.3.1 模板叙事展示。A 级 dataflow / known secret 是喂给 T3 的加分证据，**不是**单独的入队开关。

### 2.5 线索等级

- A：高价值且证据充分，优先送 T3。Semgrep 有 dataflow 且 confidence∈{HIGH,UNKNOWN}；Gitleaks `rule_class=known`。
- B：有价值但需 T3 亲审。有 locus+CWE；Gitleaks `generic` 且未落入 C。
- F：无法形成可审假设包（无 locus 且无 CWE），不调 LLM。
- C：低价值原始条目，**不形成 AlertGroup**，但保留 RawFinding 与 ScanRun 统计。产生条件（保守、表驱动）：Semgrep `confidence=LOW` 且无 dataflow；Semgrep 可得且非 security 的 category；Gitleaks `generic` 且命中占位符或落在文档/markdown 路径。缺字段时保守保留进组，不得静默当误报。

等级不再单独决定能否自动终认。

### 2.6 评估口径

发现召回、聚类纯度、AI 二审准确率、终认命中率和人工复核工作量必须分账统计。任何 prompt、模型、规则包或阈值变更必须在黄金集上重跑后再放量。不得把快审 `tp` 整包送终认当作「释放人力」。

### 2.7 合格可疑真洞（才能 dispatch）

产品优先级：**减少漏报优先于控制终认成本**。凡平台已标「可疑真洞」且字段齐备的线索，默认进入终认队；置信度只影响入队排序，**不再**作为能否入队的硬门槛。

必须**同时**满足：

1. `verdict == tp`（可疑真洞，不是误报）。
2. `verdict_source ∈ {agent, propagated}`：T3 亲审、`api_hunt` 猎洞直出（`context_log.via=api_hunt`）、或同根因族内由代表 T3 判决传播的成员。T2 `fast_model` / 规则层 `rule` / 携带 `carryover` 的 tp **禁止入队**（仍遵守 §2.6：不得把快审 tp 整包送终认）。猎洞嫌疑在字段齐备时即写 `verdict_source=agent` 判决并计 `qualified_count`；置信度不挡直出。
3. 族传播：一家族仍只让代表走 T3；成员可带传播判决入终认（各自 locus 独立 LeadRun）。传播落库时必须拷贝代表的 `evidence` 与合格门字段，否则成员会被 schema 门挡下。
4. `confidence`：**不作为入队硬门槛**；dispatch 排序仍可按置信度优先（高置信先跑）。
5. schema 强制：`why` 非空、`evidence` 非空、`summary`/`reasoning` 非空（§2.3.1）；平台拒收不合格 `submit_result`：
   - `attacker_controlled`：存在攻击者可控来源（对象 id / 角色可选；gitleaks known 密钥暴露可视为满足）
   - `reaches_sink`：能指到危险点（未授权资源读写 / 特权操作）
   - `sanitizer` ∈ `none | bypassable`（`effective` 不得报 tp / 不得进猎洞 suspects）
6. 确定性封禁：测试/文档路径上的注入/XSS 类不得入队（密钥 CWE-798 / gitleaks known 例外，与 `should_downgrade` 一致）。
7. OSV：仅 `called=true` 且 T3 认为可达才入队（未入队的 OSV 不得生成 §11.1 漏洞报告）。

## 3. 产品交互契约

### 3.1 主导航

主导航使用“工作台、项目资产、代码审计、漏洞线索、**漏洞报告**、验证环境、设置”。不得再以“漏洞验证平台”作为平台主标题；一级导航不得再单独叫「审计报告」。

「漏洞报告」页内固定两个 Tab：

| Tab | 列表粒度 | 进入后 |
|---|---|---|
| 审计报告 | discovery 任务（项目地址 / 源码版本 / 任务 ID 摘要） | 该任务下的单漏洞报告列表 → 再进单份报告正文 |
| 验证报告 | verify 任务报告（一次验证一份） | 直接阅读该次验证报告（现网行为） |

### 3.2 创建入口

默认主操作为“发起代码审计”，默认创建 `task_type=discovery`。已有明确漏洞描述时，用户可从次级入口创建 `task_type=verify`。Git 与本地上传必须支持两种模式。

### 3.3 非 Web 项目

非 Web 项目必须支持代码审计、扫描、聚类、AI 二审和白盒终认；仅依赖运行靶场的环境准备与动态复现允许跳过。靶场失败不得把整次审计标失败。

### 3.4 审计详情

审计详情优先展示审计范围、覆盖与健康度、漏洞线索统计、终认进度和报告。精确节点 DAG、事件与重试属于可展开的运行诊断，不作为主业务状态。

### 3.5 漏洞线索工作台

默认队列只展示：

- **验证中**：已入 Redis 终认队（组 `dispatched`）
- **已确认**：终认 `confirmed` / `partial`
- **代码可达**：无靶场白盒结论 `code_reachable`

不得把「已降噪 / 待复核」作为主队列。误报无展示、无复活入口。列表与详情的 AI 结论必须用「可疑真洞 / 误报 / 二审未决」，禁止把 `tp`/`fp` 当标签原文。详情展示：引擎原始命中、统一叙事（`summary`/`reasoning`）、二审依据、终认记录；当且仅当组已是 `confirmed`/`partial`/`code_reachable` 时，主区域展示**独立漏洞报告**（§11.1），并标明验证方式（靶场验证 / 代码闭环）。人工从已存在线索另开 `task_type=verify` 的次级入口可留（同一套终认节点）。

## 4. 编排契约

### 4.1 两种模式

- `discovery`：执行画像、扫描、聚类、二审、调度、多线索终认和聚合报告。
- `verify`：仅实例化 `source → profile → (audit ∥ env_ready) → reproduce → report`
  子图；不得创建一串 `VERIFY_MODE skipped` 的发现侧 NodeRun 伪装成执行轨迹。

### 4.2 调度与分支

编排器按依赖就绪渐进调度：节点 `requires` 全部 terminal（completed/skipped）即准入开跑；任一完成立即写入 terminal 并解锁下游，**同批就绪的兄弟节点不构成整波屏障**。发现侧不等待靶场；`env_ready` 在 `dispatch` 已证明有合格线索后才就绪，完成或降级后再由 LeadWorker 排空终认队列。skip 是可观察终态并满足下游依赖。分支信号包括 `non_web`、`no_dispatch_lead`、`lead_driven`、`gate_fail`、`gate_uncertain`；模式选择由子图解决，不是运行时连续 skip。

#### 4.2.2 数据前提

节点声明输入依赖和非空数据前提；缺失时按契约显式 skip 或 fail。不得靠节点内部猜测缺失输入。

#### 4.2.3 创建规则

任务类型必须显式传入，不得用“漏洞描述是否为空”推断。`verify` 必须有不少于 10 个字符的描述；`discovery` 描述必须为空。

#### 4.2.4 当前拓扑

平台保留 15 个能力节点，但按任务模式裁剪为不同子图。当前 discovery 拓扑为：

`source → profile → (scan_gitleaks ∥ scan_osv ∥ scan_semgrep ∥ api_inventory)`

`(scan_* → cluster → screen → triage) ∥ (api_inventory → api_hunt) → dispatch`

`dispatch(has_lead) → env_ready → lead audit[*] → lead reproduce[*] → report`

`screen` / `triage` 只产出判决与漏斗统计，不得创建 LeadRun、写 Redis 队列或等待终认。`dispatch` 是创建 LeadRun 和入队的唯一节点。零合格线索不起靶场；仍生成零漏洞审计摘要。

- `api_inventory`：按画像 `languages` / `frameworks` 表驱动选择确定性 parser，产出 BOM + `resource_key` 索引；获取方式分 `router`（框架路由表）、`script_file`（PHP/Next pages 式文件入口）、`export_handler`（Next `route.ts` 导出方法）、`openapi`（OpenAPI/Swagger）。PHP `script_file` 另用 tree-sitter 抽取请求传参面（超全局 + Laravel/ThinkPHP/CI/Yii 等常见封装白名单的字面量参数名）写入 `id_params`/`is_pve`；动态名与未知私有封装不保证。**禁止**用 AI 列端点；AI 不得把自拟 path 写入 BOM。详见 [`research-api-hunting.md`](research-api-hunting.md)。
- `api_hunt`：`requires=(api_inventory,)`；只审清单中的优先 PVE；若 BOM 无 `is_pve` 但仍有 `script_file` 入口，则按写操作/路径启发从 script_file 降级抽 Top-K，避免传统 PHP 永久空跑。`submit_result` 嫌疑项必须带 §2.7 合格门字段；写 `engine=api_hunt` 线索、upsert AlertGroup，并对**字段齐备**的项落 `verdict_source=agent` 判决（`qualified_count` = 直出数；置信度不挡直出）。失败不得抹杀 cluster / screen 结果。
- `cluster`：只读 `semgrep` / `gitleaks` / `osv` 的 RawFinding；不得把 `api_hunt` 并入扫描组。
- `screen`：`requires=(cluster,)`，只审扫描聚类工作集（`status=clustered` 且 `engine_set` 不含 `api_hunt`）；猎洞组无论是否已判决均不进快审。
- `dispatch`：`requires=(triage, api_hunt)`，等扫描复核链与猎洞都 terminal 后再按统一合格门入队。

### 4.3 AI 交接

AI 节点只接收结构化、已裁剪、已脱敏的输入并返回结构化结果。完整仓库路径不得交给轻量 LLM 网关；执行代码仅允许在隔离 runner 中进行。

### 4.4 多线索终认

`dispatch` 为同一审计运行把**全部合格线索**入 Redis db3 终认队，并按任务并发上限由 LeadWorker 消费。不再用「A 级 ∧ is_web」一刀切，也不是「非 fp 全进」。LeadRun 必须幂等、可恢复并具备 `queued | running | completed | failed | skipped` 状态。自动终认不创建额外 verify Task；人工从线索页放行时才创建可追溯的派生 verify Task。

预算或运行配额耗尽时，未领取的 LeadRun 必须原子转为 `skipped`，对应线索转 `needs_review`，并清理本任务 Redis 队列。任务以 `needs_review` 终态收口，不得在任务完成后留下可继续消费的队列项。

`env_ready` 失败降级为节点 `completed`（`ok=false`，无 `target_url`），不得 `_finalize_node_failure` 整任务。LeadWorker 与 verify 子图在无靶场时均只保留白盒 audit，以 `code_reachable` 收口；动态 `reproduce` 产出明确降级结果，不得因缺 `target_url` 把整任务标记为 failed。

报告渲染是分析结论的消费者。当 audit/reproduce/LeadRun 已形成终局时，`report` 失败只记录报告节点失败与错误提示，不得改写任务的权威 verdict；尚无权威结论时才允许整任务 failed。

终认工位保持两个节点职责，不另拆第三汇总节点：

| 节点 | 职责 |
|---|---|
| `audit` | 白盒终判：代码层判断漏洞是否成立，产出报告依据（`core_claim` / `gate_*` 等） |
| `reproduce` | 动态验证：仅在有 `target_url` 且 audit 允许时执行。开跑前短探活；访问失败且 Docker 已销毁/停止时重调度一次 `env_ready`，再用新 `target_url` 复现 |

每条 LeadRun 必须落 `verification_basis`：`lab`（实际跑过 reproduce 并形成有效动态证据）或 `code_path`（仅白盒终局）。discovery 下 DAG 单例 audit/reproduce 可因 `LEAD_DRIVEN` skip，但线索详情与 LeadRun 必须按两阶段展示，不得只剩空 skip。

LeadWorker 在线索终态为 `confirmed` / `partial` / `code_reachable` 时，必须触发该线索的**独立漏洞报告**生成（§11.1）；其它终态不生成。

## 5. 数据与状态

### 5.1 溯源

人工派生 verify Task 通过 `source_alert_group_id` 指回来源线索。跨 Context 使用逻辑指针，不建立 ORM relationship；服务层负责权限与一致性。

### 5.2 ScanRun

每个引擎、每次任务运行对应一个 ScanRun，记录配置摘要、状态、发现数、错误和降级信息。原始扫描条本期仍在 Postgres `raw_findings` + MinIO SARIF，不迁 Redis。

### 5.3 AlertGroup 状态机

运行期可用现表做工作集。主状态为 `new → clustered → adjudicated → dispatched → resolved`。长期可见终态为 `confirmed | partial | code_reachable`。明确误报不占组（或写后即删）。未审/未决保持非展示状态，禁止记 fp。所有状态变化必须经 FindingService。

### 5.4 模型角色

模型配置区分 screening、final、hunting 角色；`api_hunt` 优先使用 hunting，未配置时回退 default Provider，不得随机选择。轻量网关（screen）仍仅 screening/final。

### 5.5 Redis 分库

| 库 | 用途 |
|---|---|
| db0 | 事件总线 / 运行槽 |
| db1 | Celery broker |
| db2 | Celery result |
| db3 | 合格线索 + 终认队（`REDIS_CLUE_URL`） |

禁止把线索队列写到 db0（与 SSE/槽位撞命名空间），禁止写 Celery db1/2。

### 5.8 终认出口

终认结果统一为 `confirmed | partial | code_reachable | code_smell | not_reproduced | false_positive | needs_review`。`confirmed/partial` 进入已确认漏洞清单；`code_reachable` 必须进入线索台、漏洞报告与聚合审计报告正文。终认证伪只进漏斗计数，线索台不展示。

LeadRun 另记 `verification_basis ∈ {lab, code_path}`，与六档 verdict 正交：同一 `code_reachable` 必为 `code_path`；`confirmed`/`partial` 可为 `lab` 或（策略允许时）带白盒强证据的终局，但 UI 必须诚实展示验证方式。

## 6. 节点公开输出

### 6.0 Profile

画像输出多语言事实列表、主要语言、框架、包管理器、Semgrep 配置、OSV 清单和 Web 属性；每项语言事实包含文件证据和置信度。

### 6.1 Scan

扫描节点统一输出引擎、状态、发现数和 ScanRun 标识。引擎原始结果归一化为 SARIF+ Finding。Gitleaks 命中原文入库，供 owner 在线索台研判（不得 `--redact`，否则详情只剩 `REDACTED`）。OSV 必须产出可读的依赖、编号、摘要与严重度，不得只展示 CVSS 向量或漏洞 ID。发给 LLM / 日志的路径仍按 §8.2 脱敏。

### 6.2 Cluster

聚类输出原始发现数、分组数、各 CWE/引擎/等级统计及函数索引覆盖情况。必须包含降噪漏斗：`dropped_c_count`、按引擎的降噪计数（C 档未建组条目），以及 OSV bypass 组数。输入范围仅限三扫描引擎 RawFinding，排除 `api_hunt`。

### 6.2.1 API Inventory / Hunt

- `api_inventory`：输出 `endpoint_count`、`pve_count`、`parser`（汇合主键）、`parsers`、`acquisition_kinds`、`bom_path`、`unsupported_languages`、`stack_ids`、`ok`。BOM 落工作区 JSON；Handoff 只带摘要。无适配 parser 的画像语言列入 `unsupported_languages`；已跑 parser 但 0 端点**不算**语言不支持，禁止把 0 端点说成「无 API=安全」。
- `api_hunt`：输入 inventory（不依赖 cluster）；按 `resource_key` 批审 Top-K PVE（无 PVE 时对 `script_file` 降级 Top-K）；产出 `reviewed_count`、`suspect_count`、`finding_count`、`qualified_count`、`ok`。嫌疑项 schema 对齐 §2.7 + §2.3.1（`why`/`evidence`/`summary`/`reasoning`/`attacker_controlled`/`reaches_sink`/`sanitizer`/`confidence`）；归一为 RawFinding（`engine=api_hunt`）并 upsert AlertGroup；字段齐备时写 Adjudication（`verdict_source=agent`，`context_log.via=api_hunt`；**置信度不挡直出**），`qualified_count` 计此类直出数；**不进** screen/triage。验证任务 `VERIFY_MODE` skip。动态 BOLA 确认仍在 lead/reproduce，不在本节点发 HTTP。

### 6.3 Screen / Triage

二审拆为两个节点，按是否启动 Docker / Claude SDK 切分（验证任务二者均 `VERIFY_MODE` skip）；**仅覆盖扫描聚类线索**（显式排除 `engine_set` 含 `api_hunt` 的组）：

- `screen`（轻量快审）：T0 指纹携带 + T1 规则 FP 率 + T2 `llm_complete` 快审。T2 **不得**创建 LeadRun 或入队；高置信 tp 只能升级 T3。无 Docker。`requires=(cluster,)`。
- `triage`（AI 二审）：T3 族代表 + `adjudicate_group` + 族内传播；只消费仍为 `clustered` 的扫描升级组（Semgrep/Gitleaks；OSV bypass 组不在此队列）。有 Docker。T3 `submit_result` 按 §2.7 + §2.3.1 拒收假 tp / 缺叙事。本节点只落判决，不等待终认。

事件文案用「可疑真洞 N / 误报 N」，禁止只打未翻译的 `tp_count` 给用户看。`dispatch.requires = (triage, api_hunt)`。Agent 段失败时可 `retry?from_node=triage` 复用已 completed 的 `screen`，不重跑 T0–T2。旧 run 无 `screen` completed 行时不得伪造成功，须从 `screen` 或整条重试。

### 6.4 Dispatch

调度只做合格门 + 入现有终认队列。输出入队数量、线索组 ID 列表、漏斗计数（误报 / 未决 / 未审）。零合格线索是成功的审计结果，不是任务失败。删除「其余全部 `needs_review`」的默认行为。

从指定节点重试时，旧 run 的复用集合按 DAG 计算：使目标节点及其所有后代失效，复用其余已 completed/skipped 节点。禁止使用 `node_index < target_index` 代替依赖计算。

## 7. LLM 网关

轻量二审网关（`screen` 节点）不持有工具、不读取仓库、不执行代码，只消费已裁剪文本。超时、解析失败和预算耗尽必须落为可观察状态并升级 `triage`，**不得**把未审写成误报。平台级 LLM API 失败（余额不足、鉴权失败、限流等）必须中止对应二审节点并失败任务，不得伪装成「转人工」后继续调度/报告。

## 8. 安全

### 8.1 Owner 隔离

所有列表过滤条件都必须与 owner 条件合取。传入具体 task_id、group_id 或 report_id 不能绕过 owner 校验。

### 8.2 秘密脱敏

线索台（owner 可见的 RawFinding `message` / `code_snippet`）保留 Gitleaks 原始命中，便于区分测试夹具与真实凭据。扫描命令不得加 `--redact`。

发给 LLM 的 prompt、阶段事件、错误日志与失败打包仍须脱敏（保留前后缀与长度，或替换为 `[REDACTED]`）。

## 9. API 与 UI

### 9.1 漏洞线索 API

默认 `scope=workbench`：验证中 + 已确认 + 代码可达。另支持 `verifying` / `confirmed` / `reachable` / `all`。响应字段 `ai_verdict` 仍为内部码，UI 必须翻译。详情返回代表发现完整证据、统一叙事（§2.3.1）、AI 二审、自动/人工终认摘要；若已生成漏洞报告则返回报告正文与 `verification_basis`。误报无复活入口（没有入库对象）。支持单条与批量物理删除告警组（级联判决/复核/LeadRun，保留 RawFinding）；终认进行中的组不可删。人工 `POST .../groups/{id}/dispatch` 可另开 verify Task。已确认/代码可达线索必须提供独立导出（如 `GET .../groups/{id}/report/export?format=json|md`）。

### 9.2 运行进度

用户级阶段固定为“准备源码、识别技术栈、安全扫描、AI 复核、漏洞终认、生成报告”。节点图仅用于诊断，并根据任务模式隐藏无关节点。

### 9.3 漏洞报告页与 API

- 路由建议：`/reports` 为「漏洞报告」入口；`?tab=audit|verify`（或等价）切换 Tab。
- **审计报告 Tab 列表**：按 owner 的 discovery 任务聚合（有 Report 或有终局漏洞报告即可出现）；字段至少含项目、源码版本、任务创建/完成时间、确认漏洞数、代码可达数、发布状态。点击进入 `/reports/audits/{task_id}`（或等价）：页眉为任务/项目/版本与漏斗摘要；主体为该任务下单漏洞报告卡片列表（标题用 `summary`），再进入单份正文/导出。
- **验证报告 Tab 列表**：仅 `task_type=verify` 的报告，行为与现网「一次验证一份报告」一致；详情仍走现有 `/reports/{report_id}`。
- 禁止在审计报告 Tab 首页直接平铺所有单漏洞报告（必须先按任务归类）。

## 10. 评估与放量

黄金集必须覆盖支持的 CWE、语言和正负样本。评估输出按扫描、二审、终认三个阶段分账；达不到已评审阈值时禁止自动放量。回归必须覆盖「不得把快审 tp 全送终认」（禅道口径：百余条 T2 tp 不得整包入队）。另须覆盖：叙事字段齐全率、四来源终局漏洞报告结构一致、无靶场时 `verification_basis=code_path`、漏洞报告页两 Tab 导航契约。

## 11. 报告

### 11.1 单漏洞报告（discovery，一漏洞一份）

**生成时机（方案 B）**：仅当线索终认落地为 `confirmed` / `partial` / `code_reachable` 时生成；验证中、误报、未决、依赖情报 bypass 未终认成功等**不得**生成漏洞报告。

**来源无关**：Semgrep、Gitleaks、OSV、API 猎洞只要进入上述终态，必须产出同一结构的独立报告。绑定 `alert_group_id`（及对应 `lead_run_id`），挂在所属 discovery 任务下，可单独查看与导出。

最低章节：

1. 简述（`summary`）
2. 代码/依赖推理（`reasoning`，可含终认白盒修订）
3. 定位与证据
4. 来源引擎（元数据）
5. 二审或猎洞/OSV 叙事结论
6. 终认结论（六档之一）
7. 验证方式（`lab` / `code_path`）
8. 修复建议（终局必填或显式「暂缺」占位，不得静默省略章节）

### 11.2 任务级审计摘要（discovery，一任务一份）

每个完成的 discovery 运行都生成任务级聚合摘要（现 Report 行），即使确认漏洞数为零。用于审计报告 Tab 的任务列表与任务页眉：审计范围、源码版本、画像、扫描器健康度、发现漏斗、终认计数，以及对 §11.1 单漏洞报告的索引。不得把未审写成待人工 0 却与库内漏斗矛盾。发布后版本冻结规则仍适用。

### 11.3 验证报告（verify，一次验证一份）

`task_type=verify`：**一次验证任务对应一份验证报告**（现网行为），进入「漏洞报告 → 验证报告」Tab。结构可与历史验证报告兼容；若由线索派生，应能链回 `source_alert_group_id`，但列表粒度仍是验证任务，不是审计任务下的多漏洞列表。

## 12. 判定语义

任务状态与漏洞判定分离：任务可成功完成但没有确认漏洞；工具部分降级（含靶场失败）时可完成并标记审计不完整；任何关键阶段未执行且无法降级时进入 `needs_review` 或 `failed`。不得使用空报告、空 verdict 或 HTTP 成功掩盖未定义状态。
