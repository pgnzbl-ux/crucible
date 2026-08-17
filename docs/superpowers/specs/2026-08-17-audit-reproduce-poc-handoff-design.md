# 审计射击清单 → 复现直打 → 完整 PoC 入库

> 版本: v1.0 · 2026-08-17
> 状态: 已评审
> 定位: 让白盒审计产出可直接执行的 HTTP 射击清单；复现第一枪必须打这份清单，失败后可在同一容器内深挖同一条核心主张（不设次数上限）；打通后由 reproduce 写完整 PoC 入库，报告页用现有 Markdown 展示。
> 覆盖并修正：`2026-08-12-platform-node-orchestration-design.md` §1.3 audit/reproduce 交接与 reproduce「5 次变体」；`2026-08-14-reproduce-live-gate-design.md` 变体上限与 `poc_commands` 由 report 撰写；`docs/agent-workflow.md` 回退环；audit/reproduce 蒸馏 skill。
> 阅读对象: 编排 / ai_runner schema / 节点 skill / 报告落库 / 报告页。

---

## 0. 已确认决策

| 项 | 决定 |
|---|---|
| 第一枪 | reproduce **必须**把 audit `payloads[]` 拼到注入的 `target_url` + `initial_creds` 上发 HTTP。禁止第一枪另选端点或先去「找更好的洞」 |
| 失败后 | 允许复现员重读源码、修正利用链/编码/路径/鉴权。必须仍测审计的 **同一条** `core_claim` |
| 次数 | **不设验证轮询上限**。平台不按次数循环，skill 不写「最多 5 次」。Claude 判定不存在或无法成功即 `submit_result` 停手 |
| 平台停手 | 仅：模型已提交判定、容器/Celery/SDK `maxTurns` 超时、用户取消。超时是防挂死，不是验证轮数 |
| 换题 | 禁止。深挖不得改成另一条危害 / 另一个 CWE / 黑盒扫靶场 |
| PoC 何时 | 仅 `confirmed` / `partial`。其余 4 档禁止 PoC |
| PoC 语言 | Python 完整脚本优先 → bash 完整脚本 → 其它完整程序（须写 `language_reason`） |
| PoC 完整 | 可独立运行；禁止 `TODO` / 截断 / 占位 token；请求必须是已打出危害的那条；成功判据写在脚本里 |
| PoC 作者 | **reproduce 写正本**。report **不得重写代码**；`report_data.poc_commands` 由平台从正本生成 Markdown 围栏 |
| 存储 | `reports` 表独立列：`poc_language` / `poc_filename` / `poc_code` / `poc_usage`。详情 API 带回 |
| 展示 | 现有 `MarkdownBody`。不用 CodeMirror、不加新编辑器依赖 |
| 节点顺序 | 仍是 env_ready → audit → reproduce → report。audit 仍禁止 HTTP |
| 编排出口 | 不变：`fail` skip reproduce 仍跑 report；`uncertain` skip 4+5 |

明确不做：对调 audit/env_ready、拆「写 PoC」为第 7 节点、平台按次数循环、机械证伪 HTTP 危害、给 runner 加 Chromium、用 CodeMirror、把 PoC 只放 MinIO 当唯一来源。

---

## 1. 问题与目标

### 1.1 现状偏离

- audit `payloads` 是无类型数组，常交出 `' OR 1=1` 字符串。reproduce 无法「拿来就发」。
- reproduce skill 只软约束「测核心主张」「PoC 当假设」，不强制第一枪执行审计模板；`kill_chain` / `defense_layers` 未点名消费。
- skill 写「变体上限 5 次」，与「让模型判断不存在就停」冲突。
- `poc_commands` 由 report 节点用 Markdown 撰写，易变成示意 curl，不是已打通的完整脚本；正本不在独立列，前端只能当普通 Markdown 节。

### 1.2 目标闭环

```
audit gate=pass
  交出 core_claim + 请求模板 payloads[]
        │
        ▼
reproduce 第一枪 = 执行模板（拼 target_url / creds）
        │
        ├─ 观察到 core_claim 的 HTTP 危害
        │     → confirmed（或更窄危害 → partial）
        │     → 交完整 poc {language, filename, code, usage}
        │
        └─ 未打出 → 同容器深挖同一 core_claim（无次数上限）
              ├─ 最终打出 → 同上
              ├─ 结构性不存在 → false_positive，无 poc
              └─ 运行时条件无法满足 / 无法成功 → not_reproduced，无 poc

report
  漏洞报告：平台用 reproduce.poc 覆盖 poc_commands 围栏
  验证记录：禁止 poc 列与 poc_commands

落库
  reports.poc_* 列 + report_data.poc_commands（生成态）
  前端 §6 优先渲 poc_code
```

节点失败（无 `submit_result` / 形状不合格 / 容器挂）→ 现有 `task=failed`，不是「再试一轮」。

---

## 2. 审计产出（节点 3）

平台仍只校验形状并路由，不判 kill_chain 真伪。

### 2.1 `pass` 形状（在现有 `kill_chain` / `runtime_dependent` / `defense_layers` 之上）

必填新增：

- `core_claim`：非空字符串。一条 HTTP 可观察危害。reproduce 全程不得换题。
- `payloads`：长度 ≥ 1 的**对象数组**，不再接受纯字符串。

每条 payload 必填：

| 字段 | 类型 | 约束 |
|---|---|---|
| `method` | string | 非空，建议大写 HTTP 方法 |
| `path` | string | 非空相对路径（以 `/` 开头）。reproduce 拼到 `target_url`，禁止 payload 里写死 localhost |
| `expected_observable` | string | 非空。成功时长什么样 |

可选：`headers`（object）、`body`（string）、`content_type`（string）。

`runtime_dependent === true` 时另必填 `unresolved_facts`：非空字符串数组（缺登录态 / CSRF / 资源 ID 等）。`false` 时该字段可缺省或 `[]`。

`fail` / `uncertain` 形状相对现状不变。`uncertain` 仍不得带非空 `payloads`。

### 2.2 Mock

`_mock_output("audit")` 的 `payloads` 改为一条合法对象；补 `core_claim`；`runtime_dependent=false` 时可不给 `unresolved_facts`。

### 2.3 Skill

`node-skills/audit/SKILL.md`：Phase 2「还原真实请求格式」必须落到 `payloads[]` 对象，不得只写进 `kill_chain` 长文。禁止把 payload 写成无法发的片段字符串。

---

## 3. 复现（节点 4）

### 3.1 输入

现有字段不变，另依赖 `audit.core_claim` 与对象形 `payloads`。仍不注入 `profile`（YAGNI；源码与审计清单足够第一枪）。

### 3.2 工作流（skill 正文，平台不解析步骤）

1. **第一枪（强制）**：执行 `payloads[0]`：`url = target_url.rstrip('/') + path`，带上 `initial_creds`。其余 payload 是后续候选，不是必须先全部打完才能深挖。`runtime_dependent` 时先补 `unresolved_facts` 再发攻击请求。
2. **记录**：每次 HTTP 仍写入 `attempts[]`（现有 6 字段）。
3. **未打出危害**：允许 Grep/Read 源码、改同一入口的编码/绕过/参数形态。禁止换 `core_claim`、禁止 docker、禁止黑盒扫。
4. **停手**：能诚实判定 6 档之一就 `submit_result`。不要为凑成功而空转；也不要因为「还没试满 N 次」而硬停。
5. **打通后**：用**已打出危害的请求**写完整 PoC，再 `submit_result`。

删除 skill 与 `docs/agent-workflow.md` 中「上限 5 次 / 回退环最多 5 次」表述。改为「容器内自行深挖，判定即停」。

### 3.3 产出 `poc`

`confirmed` / `partial` 必填对象：

| 字段 | 约束 |
|---|---|
| `language` | `python` \| `bash` \| `other` |
| `filename` | 非空。python 默认 `poc.py`，bash 默认 `poc.sh` |
| `code` | 非空完整源码。`strip()` 后长度 ≥ 1 |
| `usage` | 非空一行，例如 `python poc.py --url http://host.docker.internal:8080` |
| `language_reason` | `language != python` 时必填非空 |

其余 verdict：不得出现非空 `poc`（缺省或 `null`）。有 `code` 则形状失败。

平台形状检查（不跑脚本、不解析 AST）：

- `language` 枚举
- `filename` / `code` / `usage` 非空字符串
- 非 python 时 `language_reason` 非空
- **不**扫描 `TODO`（文案约束，避免误杀）

XSS/DOM 无浏览器约束不变：仅 curl 不得 `confirmed`。

### 3.4 Mock

`_mock_output("reproduce")` 补合法 python `poc`（短但完整的 `if __name__` 脚本，不是 `curl` 一行）。

---

## 4. 报告（节点 5）与落库

### 4.1 正本覆盖

`ReportNode.execute` 在调用 `run_ai_node` **之后**：

1. 从 `previous_outputs["reproduce"]` 取 `poc`。
2. 若权威 verdict 为 `confirmed`/`partial`：必须有合法 `poc`，否则本节点失败（reproduce 形状本应已拦住）。
3. **用代码**把 `report_data.poc_commands` 写成：

````markdown
```<fence_lang>
<code>
```

用法：`<usage>`
````

`fence_lang`：`python` → `python`，`bash` → `bash`，`other` → `text`。

4. 把 reproduce 的 `poc` 对象原样挂到本节点 output（键名 `poc`）。落库层再拆成四列。**丢弃**模型自己写的 `poc_commands`。

验证记录路径：确保无 `poc`、无 `poc_commands`（现有校验已禁）。

Skill：报告员禁止改写 PoC 源码；§6 会被平台覆盖。漏洞报告仍须交其余 7 节 Markdown。

### 4.2 `reports` 表

新增可空列：

| 列 | 类型 | 说明 |
|---|---|---|
| `poc_language` | String(16) | `python`/`bash`/`other` |
| `poc_filename` | String(255) | 如 `poc.py` |
| `poc_code` | Text | 完整源码 |
| `poc_usage` | String(1024) | 一行用法 |

未确认判定四列均为 NULL。Alembic 迁移；SQLite/PostgreSQL 均可。

`report_columns_from_orch_result` / `run_orchestration` 返回值带上 poc，供 `tasks.py` 建 `Report` 时写入。旧报告四列为空。

### 4.3 API

`GET /api/v1/reports/{id}` 的 `ReportDetail` 增加四字段（可空）。列表接口不加 poc 正文（YAGNI）。

`.claude/api-contract.md`：漏洞报告必有 poc 列（confirmed/partial）；`poc_commands` 节改为「平台从 poc_code 生成的围栏，正本是列」。

导出 Markdown：现有 `render_report_md` 读已覆盖后的 `report_data.poc_commands`，无需另拼。

---

## 5. 前端

`ReportContent` §6 `poc_commands`：

- 详情带非空 `poc_code`：拼与 §4.1 相同的围栏 Markdown，交给现有 `MarkdownBody`。节标题旁展示 `poc_filename`（纯文本，无下载按钮）。
- 否则回退 `report_data.poc_commands`（旧报告）。
- 验证记录：不渲染 poc 列，保持「未形成漏洞 PoC」Alert。

不加 `@codemirror/*`、不加新编辑器。`frontend/package.json` 不为此增依赖。

OpenAPI 生成类型若手抄 `ReportDetail`，同步四字段。

---

## 6. 文档同步（实施时）

| 文档 | 改什么 |
|---|---|
| `2026-08-12-platform-node-orchestration-design.md` §1.3 | audit pass 模板；reproduce 无 5 次上限；产出含 `poc` |
| `docs/agent-workflow.md` | 回退环改为「判定即停」；reproduce 交 PoC 正本 |
| `2026-08-14-reproduce-live-gate-design.md` | 文首注明被本设计覆盖的变体上限 / poc 作者 |
| `.claude/api-contract.md` | ReportDetail poc 列 |
| `docs/development-guide.md` | 仅当完成清单需要点名时改一句 |

桌面插件 `plugins/vuln-verify-expert` **不**改（母本不当 runtime）。

---

## 7. 测试

先红后绿。至少：

| 用例 | 期望 |
|---|---|
| audit pass 的 payload 为字符串 | `validate_output` 失败，错误含 `payloads` |
| audit pass 缺 `core_claim` / 缺 `path` | 失败 |
| audit pass + `runtime_dependent=true` 缺 `unresolved_facts` | 失败 |
| reproduce confirmed 缺 `poc` | 失败 |
| reproduce false_positive 带非空 `poc.code` | 失败 |
| reproduce mock 含合法 python poc | 通过 |
| ReportNode 覆盖：模型写的 `poc_commands` 被正本替换 | 断言围栏内是 reproduce 的 `code` |
| `report_columns_from_orch_result` 抽出 poc 四列 | confirmed 有值；false_positive 为空 |
| 前端：有 `poc_code` 时 Markdown 含围栏；无则回退旧节 | 组件测试 |
| orchestrator：gate fail 仍 skip reproduce，报告无 poc | 现有测试不回退 |

不新增「试了几次」计数测试。不测脚本能否真跑通（平台不执行 PoC）。

---

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 无次数上限导致长会话烧 token | 已有 SDK `maxTurns`、容器超时、Celery `time_limit`；skill 要求判定即停 |
| 深挖换题 | 文案强制 `core_claim`；平台不解析是否换题 |
| 报告模型改写 PoC | 平台覆盖 `poc_commands` + 独立列存正本 |
| 旧任务/旧报告 | 校验只卡新节点产出；前端 poc_code 空则回退 Markdown 节 |

---

## 附: 决策溯源

| 决策 | 选项 | 选择 | 理由 |
|---|---|---|---|
| 失败后深挖范围 | 只改 payload / 可改链但不换主张 / 可换洞 | 可改链不换主张 | 任务模型是验证这一条上报，不是项目挖洞 |
| 停手条件 | 最多 5 次 / 模型判定即停 | 判定即停 | 次数是假精度；模型能判断不存在 |
| PoC 展示 | CodeMirror / Markdown 围栏 | Markdown | 已有 `MarkdownBody`，无新依赖 |
| PoC 存储 | 只在 report_data / 独立列+生成节 | 独立列+生成节 | 正本可查、报告模型无法污染 |
| PoC 作者 | report 写 / reproduce 写 | reproduce | 同会话刚打通，少一轮转述失真 |
