# Crucible 代码审计与漏洞挖掘规格

> 状态：已确认  
> 版本：1.1  
> 业务定位：AI 辅助下的代码审计漏洞挖掘平台

## 1. 产品边界

### 1.1 平台主语

Crucible 的主流程是“项目资产 → 代码审计 → 漏洞线索 → 终认 → 审计报告”。
漏洞验证不是独立平台定位，而是代码审计中的终认能力；对用户已有明确线索时，平台同时提供次级的定向验证入口。

### 1.2 用户对象

- 项目资产（Project）：待审计的 Git 仓库或上传源码包。
- 代码审计（Task，`task_type=discovery`）：对一个确定源码版本执行发现侧流水线。
- 漏洞线索：扫描聚类后经二审裁剪，**仅合格可疑真洞**进入终认队；误报不作为用户可见对象长期占库。
- 终认记录（LeadRun 或派生 verify Task）：对单条合格线索执行白盒审计与可选动态复现。与 `task_type=verify` 共用同一套 AuditNode / ReproduceNode，不另起产品。
- 审计报告（Report）：一次代码审计的完整结果，不以是否确认漏洞为生成前提。

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

终认另有一套词：`confirmed` / `partial` = 已确认；`false_positive` = 终认证伪；`code_reachable` = 无靶场时的代码可达。全程都在当前 13 节点编排里。

## 2. 发现质量规则

### 2.1 发现来源

首发发现源为 Semgrep、Gitleaks、OSV。每个引擎独立执行、独立记账；单引擎失败不得抹去其它引擎结果。

### 2.2 反锚定

AI 二审默认不接收静态引擎的最终结论措辞，只接收规则身份、定位、代码上下文和数据流证据。人工定向验证默认也不附带引擎结论，用户显式选择时除外。

### 2.3 HypothesisPack

每条进入 AI 工位或终认工位的线索必须是单一、封闭的假设包，至少包含 CWE、文件位置、函数、证据片段、可用数据流和待证明条件。禁止把多条无关线索拼入同一 prompt。

### 2.4 聚类与二审

确定性代码降噪必须在 AI 二审之前完成。归一化阶段把引擎结构化信号写入 Finding 的 `raw`（不另建列）：

| 引擎 | 降噪可用信号 |
|---|---|
| Semgrep | `confidence`（HIGH/MEDIUM/LOW/UNKNOWN）、`category`、`has_dataflow`（与 codeFlows 一致） |
| Gitleaks | `rule_class`（known \| generic）、可选 `entropy` |
| OSV | `called`（bool\|null）、`unimportant`（若有）；依赖组件名/版本 |

原始发现经降噪后再按任务、CWE、文件、函数及重叠行区间聚类。AI 二审只接收等级 A/B 的假设包；输出 `tp | fp | need_more_context | parse_failed | bypass`、置信度、理由、证据和（可疑真洞时）合格门字段。二审**不是**终认：它只在切片上回答三问（来源与约束、消毒能否绕过、路径是否可达），禁止 HTTP、禁止全仓挖洞。

- **明确误报 `fp`**：不展示、不写（或立刻删）Postgres 线索组，只加漏斗计数。无复活入口。
- **二审未决 / 预算未审**：漏斗「未覆盖或未决」，不标误报、不进终认。
- **合格可疑真洞**：见 §2.7，才进入 Redis 终认队，交给 LeadWorker。

OSV 不是 SAST。不得把全部 GHSA 当白盒真洞；仅当本仓库**调用了**受影响组件 API（`called`）且 T3 认为可达，才允许入队；否则只进漏斗「依赖情报」计数。A 级 dataflow / known secret 是喂给 T3 的加分证据，**不是**单独的入队开关。

### 2.5 线索等级

- A：高价值且证据充分，优先送 T3。Semgrep 有 dataflow 且 confidence∈{HIGH,UNKNOWN}；Gitleaks `rule_class=known`。
- B：有价值但需 T3 亲审。有 locus+CWE；Gitleaks `generic` 且未落入 C。
- F：无法形成可审假设包（无 locus 且无 CWE），不调 LLM。
- C：低价值原始条目，**不形成 AlertGroup**，但保留 RawFinding 与 ScanRun 统计。产生条件（保守、表驱动）：Semgrep `confidence=LOW` 且无 dataflow；Semgrep 可得且非 security 的 category；Gitleaks `generic` 且命中占位符或落在文档/markdown 路径。缺字段时保守保留进组，不得静默当误报。

等级不再单独决定能否自动终认。

### 2.6 评估口径

发现召回、聚类纯度、AI 二审准确率、终认命中率和人工复核工作量必须分账统计。任何 prompt、模型、规则包或阈值变更必须在黄金集上重跑后再放量。不得把快审 `tp` 整包送终认当作「释放人力」。

### 2.7 合格可疑真洞（才能 dispatch）

必须**同时**满足：

1. `verdict == tp`（可疑真洞，不是误报）。
2. `verdict_source == agent`：只有 T3 亲审。T2 `fast_model` 的 tp **禁止入队**，只能标 fp 或升级 T3。
3. 族内传播的 tp **不入队**；一家族只让代表走 T3。
4. `confidence >= triage_high_confidence`（现 0.8）。
5. schema 强制：`why` 非空、`evidence` 非空；平台拒收不合格 `submit_result`：
   - `attacker_controlled`：存在攻击者可控来源（gitleaks known 密钥暴露可视为满足）
   - `reaches_sink`：能指到危险点
   - `sanitizer` ∈ `none | bypassable`（`effective` 不得报 tp）
6. 确定性封禁：测试/文档路径上的注入/XSS 类不得入队（密钥 CWE-798 / gitleaks known 例外，与 `should_downgrade` 一致）。
7. OSV：仅 `called=true` 且 T3 认为可达才入队。

## 3. 产品交互契约

### 3.1 主导航

主导航使用“工作台、项目资产、代码审计、漏洞线索、审计报告、验证环境、设置”。不得再以“漏洞验证平台”“验证报告”作为平台主标题。

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

不得把「已降噪 / 待复核」作为主队列。误报无展示、无复活入口。列表与详情的 AI 结论必须用「可疑真洞 / 误报 / 二审未决」，禁止把 `tp`/`fp` 当标签原文。详情仍展示代码片段、数据流、AI 判定依据和终认记录。人工从已存在线索另开 `task_type=verify` 的次级入口可留（同一套终认节点）。

## 4. 编排契约

### 4.1 两种模式

- `discovery`：执行画像、扫描、聚类、二审、调度、多线索终认和聚合报告。
- `verify`：跳过发现侧扫描链，使用人工描述直接执行白盒审计、可选动态复现和单漏洞报告。

### 4.2 调度与分支

编排器按依赖就绪波次执行，skip 是可观察终态并满足下游依赖。分支信号包括 `verify_mode`、`non_web`、`no_dispatch_lead`、`lead_driven`、`gate_fail`、`gate_uncertain`。

#### 4.2.2 数据前提

节点声明输入依赖和非空数据前提；缺失时按契约显式 skip 或 fail。不得靠节点内部猜测缺失输入。

#### 4.2.3 创建规则

任务类型必须显式传入，不得用“漏洞描述是否为空”推断。`verify` 必须有不少于 10 个字符的描述；`discovery` 描述必须为空。

#### 4.2.4 当前拓扑

当前拓扑为 `source → profile → scans/env_ready → cluster → screen → triage → dispatch → lead verification → report`。实现可把三个扫描引擎拆为独立节点；产品 UI 不依赖固定节点数量。`env_ready` 与扫描并行；零线索仍可能起靶场（本期不搬拓扑）。

### 4.3 AI 交接

AI 节点只接收结构化、已裁剪、已脱敏的输入并返回结构化结果。完整仓库路径不得交给轻量 LLM 网关；执行代码仅允许在隔离 runner 中进行。

### 4.4 多线索终认

`dispatch` 为同一审计运行把**全部合格线索**入 Redis db3 终认队，并按任务并发上限由 LeadWorker 消费。不再用「A 级 ∧ is_web」一刀切，也不是「非 fp 全进」。LeadRun 必须幂等、可恢复并具备 `queued | running | completed | failed | skipped` 状态。自动终认不创建额外 verify Task；人工从线索页放行时才创建可追溯的派生 verify Task。

`env_ready` 失败降级为节点 `completed`（`ok=false`，无 `target_url`），不得 `_finalize_node_failure` 整任务。LeadWorker 无靶场时只跑白盒 audit，可出 `code_reachable`；只跳过动态 `reproduce`。

## 5. 数据与状态

### 5.1 溯源

人工派生 verify Task 通过 `source_alert_group_id` 指回来源线索。跨 Context 使用逻辑指针，不建立 ORM relationship；服务层负责权限与一致性。

### 5.2 ScanRun

每个引擎、每次任务运行对应一个 ScanRun，记录配置摘要、状态、发现数、错误和降级信息。原始扫描条本期仍在 Postgres `raw_findings` + MinIO SARIF，不迁 Redis。

### 5.3 AlertGroup 状态机

运行期可用现表做工作集。主状态为 `new → clustered → adjudicated → dispatched → resolved`。长期可见终态为 `confirmed | partial | code_reachable`。明确误报不占组（或写后即删）。未审/未决保持非展示状态，禁止记 fp。所有状态变化必须经 FindingService。

### 5.4 模型角色

模型配置区分 screening、final、hunting 角色；未配置特定角色时按平台已声明的回退规则处理，不得随机选择。

### 5.5 Redis 分库

| 库 | 用途 |
|---|---|
| db0 | 事件总线 / 运行槽 |
| db1 | Celery broker |
| db2 | Celery result |
| db3 | 合格线索 + 终认队（`REDIS_CLUE_URL`） |

禁止把线索队列写到 db0（与 SSE/槽位撞命名空间），禁止写 Celery db1/2。

### 5.8 终认出口

终认结果统一为 `confirmed | partial | code_reachable | code_smell | not_reproduced | false_positive | needs_review`。`confirmed/partial` 进入已确认漏洞清单；`code_reachable` 必须进入线索台与报告正文。终认证伪只进漏斗计数，线索台不展示。

## 6. 节点公开输出

### 6.0 Profile

画像输出多语言事实列表、主要语言、框架、包管理器、Semgrep 配置、OSV 清单和 Web 属性；每项语言事实包含文件证据和置信度。

### 6.1 Scan

扫描节点统一输出引擎、状态、发现数和 ScanRun 标识。引擎原始结果归一化为 SARIF+ Finding，并在落库前脱敏。

### 6.2 Cluster

聚类输出原始发现数、分组数、各 CWE/引擎/等级统计及函数索引覆盖情况。必须包含降噪漏斗：`dropped_c_count`、按引擎的降噪计数（C 档未建组条目），以及 OSV bypass 组数。

### 6.3 Screen / Triage

二审拆为两个节点，按是否启动 Docker / Claude SDK 切分（验证任务二者均 `VERIFY_MODE` skip）：

- `screen`（轻量快审）：T0 指纹携带 + T1 规则 FP 率 + T2 `llm_complete` 快审。T2 **不得**对 tp 走 LeadStreamer；高置信 tp 只能升级 T3。无 Docker。
- `triage`（AI 二审）：T3 族代表 + `adjudicate_group` + 族内传播；只消费仍为 `clustered` 的升级组。有 Docker。T3 `submit_result` 按 §2.7 拒收假 tp。

事件文案用「可疑真洞 N / 误报 N」，禁止只打未翻译的 `tp_count` 给用户看。`dispatch.requires = triage`。Agent 段失败时可 `retry?from_node=triage` 复用已 completed 的 `screen`，不重跑 T0–T2。旧 run 无 `screen` completed 行时不得伪造成功，须从 `screen` 或整条重试。

### 6.4 Dispatch

调度只做合格门 + 入现有终认队列。输出入队数量、线索组 ID 列表、漏斗计数（误报 / 未决 / 未审）。零合格线索是成功的审计结果，不是任务失败。删除「其余全部 `needs_review`」的默认行为。

## 7. LLM 网关

轻量二审网关（`screen` 节点）不持有工具、不读取仓库、不执行代码，只消费已裁剪文本。超时、解析失败和预算耗尽必须落为可观察状态并升级 `triage`，**不得**把未审写成误报。平台级 LLM API 失败（余额不足、鉴权失败、限流等）必须中止对应二审节点并失败任务，不得伪装成「转人工」后继续调度/报告。

## 8. 安全

### 8.1 Owner 隔离

所有列表过滤条件都必须与 owner 条件合取。传入具体 task_id、group_id 或 report_id 不能绕过 owner 校验。

### 8.2 秘密脱敏

秘密值在 RawFinding 落库前脱敏，只保留必要前后缀与长度信息。prompt、事件、错误、报告和导出物不得包含原始秘密。

## 9. API 与 UI

### 9.1 漏洞线索 API

默认 `scope=workbench`：验证中 + 已确认 + 代码可达。另支持 `verifying` / `confirmed` / `reachable` / `all`。响应字段 `ai_verdict` 仍为内部码，UI 必须翻译。详情返回代表发现完整证据、AI 二审以及自动/人工终认摘要。误报无复活入口（没有入库对象）。支持单条与批量物理删除告警组（级联判决/复核/LeadRun，保留 RawFinding）；终认进行中的组不可删。人工 `POST .../groups/{id}/dispatch` 可另开 verify Task。

### 9.2 运行进度

用户级阶段固定为“准备源码、识别技术栈、安全扫描、AI 复核、漏洞终认、生成报告”。节点图仅用于诊断，并根据任务模式隐藏无关节点。

## 10. 评估与放量

黄金集必须覆盖支持的 CWE、语言和正负样本。评估输出按扫描、二审、终认三个阶段分账；达不到已评审阈值时禁止自动放量。回归必须覆盖「不得把快审 tp 全送终认」（禅道口径：百余条 T2 tp 不得整包入队）。

## 11. 审计报告

每个完成的 discovery 运行都生成审计报告，即使确认漏洞数为零。报告至少包含审计范围、源码版本、画像、扫描器健康度、发现漏斗（与线索台同一套合格/终态计数）、终认结果、**代码可达**正文、确认漏洞详情和修复建议。不得把未审写成待人工 0 却与库内漏斗矛盾。报告发布后正文版本冻结；新增证据形成新版本或补充附件记录。

## 12. 判定语义

任务状态与漏洞判定分离：任务可成功完成但没有确认漏洞；工具部分降级（含靶场失败）时可完成并标记审计不完整；任何关键阶段未执行且无法降级时进入 `needs_review` 或 `failed`。不得使用空报告、空 verdict 或 HTTP 成功掩盖未定义状态。
