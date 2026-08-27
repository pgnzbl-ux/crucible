# report_data 逐节写作指南

平台 `report_data` 是 **8 个 Markdown 字符串键**（不是整份 md 文件）。本指南把 plugin 模板的硬要求映射到每个键：写什么、多细、哪些平台会校验或覆盖。

## 通用规则（两个 document_kind 都适用）

- 每键一个 Markdown 字符串；键内可用 `**加粗**`、列表、表格，但**不要**写 `## 1.` 这类编号大标题（平台负责节序）。
- 中文；口述式、面向"要动手复现的同事"，不出现 Playwright/自动化/AI 字眼。
- 截图：只引用 reproduce 提供的真实图片路径，行内嵌 `![说明](路径)`——**禁止**只写文字路径；没有截图就自然叙述，不造假。
- `poc_commands`：平台会用 reproduce 的 `poc` 正本覆盖，你只交非空占位；禁止改写 `poc.code`。

## document_kind = vulnerability_report（confirmed / partial）

| 键 | 必须包含 |
|---|---|
| `product_intro` | 一段话产品定位（从 README/代码推断）+ 本次验证的版本/commit |
| `vulnerability` | 属性表：类型（CWE-XXX）、CVSS 向量+分数+等级（照抄 reproduce.cvss，不得改判）、漏洞文件:行号、前置条件、触发入口；加一句核心危害与触发条件默认值是否满足 |
| `impact` | 受影响/不受影响范围；**利用性依赖部署实况时在此写分场景表**（默认凭据 vs 已改密 vs 强口令），注明"主定级取本报告验证场景"——分级依据见 reproduce 侧评分规范 |
| `details` | 4.1 代码审计分析（文件:行号 + 缺陷代码原文 + 防御层逐层分析）+ 4.2 PoC 构造思路（端点选择、绕过方式、payload 设计） |
| `reproduction` | 口述式：**前因（环境前提，必须含 transport shape：协议/端口/TLS 终结点/反代与 X-Forwarded-Proto 信任关系/信道检查是否命中）→ 操作步骤 → 期望 → 实际**，取材于 reproduce.attempts 里真正打出危害的请求；有截图路径就逐步行内嵌入 |
| `poc_commands` | 非空占位（平台覆盖） |
| `fix_suggestions` | 按防御层给修复（代码层 / 配置层 / 纵深防御），可操作、可验收 |
| `reporting_decision` | 报送建议是参考：按目标部署实况提示选场景定级，避免高估（报送驳回）或低估 |

## document_kind = verification_record（code_reachable / code_smell / false_positive / not_reproduced / needs_review）

| 键 | 必须包含 |
|---|---|
| `product_intro` | 同上 |
| `claimed_issue` | 原主张：类型标签 + 平台投影的核心主张（`audit.core_claim`）+ 来源（审计线索/人工描述） |
| `whitebox_analysis` | 走链结论：输入→防御层→sink 的映射；哪些防御层确认拦截/为何断链（引用 `audit.gate_reason`/`kill_chain`/`unresolved_facts`） |
| `test_record` | 逐条粘贴 reproduce.attempts 的原始请求、状态码、响应摘要、观察与 result 类型——**含失败探测**，不得只留成功的 |
| `blocker` | 阻断原因分层写清：结构性防御 / 运行时条件（含 transport 不匹配）/ 环境缺失 / 待人工疑点（needs_review） |
| `observed_facts` | 仅记已观察事实（响应、状态、日志），与"危害已确认"严格区分 |
| `remaining_conditions` | 复现还需什么（TLS 连接器、特定账号、特定数据态），给后续人工指路 |
| `reporting_decision` | 建议与理由；needs_review 时写明"待人工复核的疑点清单" |

## 硬校验回顾（平台会拒绝的形状）

- `final_verdict` == 输入 `expected_verdict`；`document_kind` == 输入值。
- vulnerability_report：**禁止**出现"未复现/误报"式改判；verification_record：**禁止** cvss、**禁止** poc_commands、**禁止**把"没打通"写成已复现。
- 每键非空字符串；不要嵌套 JSON 对象。
