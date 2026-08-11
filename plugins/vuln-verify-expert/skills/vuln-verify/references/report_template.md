# 漏洞报告：<产品名> <漏洞简述>

> **生成器硬约束** — `scripts/md_to_docx.py` 在生成 docx 之前会校验下列项目,缺失会 fail-fast。
> 校验是 **transport-agnostic** 的:HTTPS / WebSocket / gRPC / MQTT 等任意 transport 都能通过,
> 只要该 transport 下的连接器 / 端口 / 信道检查描述在 §5.1 写得清楚:
>
> 1. 全部 8 个编号节齐全(`## 1.` 至 `## 8.`)
> 2. `### 5.1 环境准备` 必须包含 **transport-shape 描述**(HTTPS 写 TLS / 端口 / 终结点;HTTP 写监听器 / 端口;WebSocket 写 ws/wss 路径;gRPC 写 metadata 透传 等)
> 3. `### 5.2 复现步骤` **每个"步骤 N"** 必须紧跟一行 `![<alt>](img/stepN_xxx.png)` 截图
> 4. 截图文件实际存在于 `img/` 目录下
> 5. **任何 transport 的"反向代理/端口转发等影响中间链"因素都需要在本节自证**(HTTPS: 自签 / mkcert / system trust;H2C / WebSocket: 升级头 / `Upgrade: h2c` 落地;gRPC: trailer 处理;等)

## 1. 产品介绍
<一段话介绍产品，从 README 或代码推断。>

## 2. 漏洞描述

| 属性 | 值 |
|------|-----|
| **漏洞类型** | CWE-XXX: 漏洞类型名称 |
| **CVSS 3.1** | X.X Severity (向量字符串) |
| **漏洞文件** | `path/to/file.php` (行号) |
| **前置条件** | 需要什么权限/登录状态 |
| **触发入口** | URL（前台/后台，是否需认证） |

- 核心危害：一句话描述
- 环境限制：什么条件下可利用
- 触发条件默认值：该条件在默认安装下是否满足

## 3. 影响范围
- 受影响版本
- 不受影响版本
- 触发条件及默认值

## 4. 漏洞详情
### 4.1 代码审计分析
<文件路径:行号，漏洞代码原文，缺陷分析>
### 4.2 PoC 构造思路
<端点选择原因、防护绕过方式、payload 设计考量、利用链>

## 5. 漏洞复现
### 5.1 环境准备  *(MUST 含 transport-shape 描述 — transport-agnostic)*

- 目标: <产品名> <版本> on <操作系统/Web服务>
- **Transport shape (MUST)**:
  - 对 **HTTP/HTTPS**: `<host:port>` → `<协议/版本>`,TLS 终结点(应用 / 反代 / 旁路),
    反代是否透传 `X-Forwarded-Proto`,后端是否仅信任代理段(非公网)。
  - 对 **WebSocket / WebSocket Secure**: `ws://` 或 `wss://`,代理是否支持
    `Upgrade: websocket`,认证机制(`Sec-WebSocket-Protocol` cookie? Basic Auth? token?)。
  - 对 **gRPC / gRPC-web**: `h2` / `h2c`、HTTP/2 必须支持、metadata 头透传链、
    trailer 处理。
  - 对 **MQTT / AMQP / QUIC**: 监听端口、TLS 方式(8910/8883/SNI)、ACL 命中、
    客户端 trust store 配置。
  - 对 **任何 transport**: `requires-channel` / `<transport-guarantee>` /
    `trust proxy` / `RemoteIpValve` 等"信道检查"在当前 connector 下是否命中。
- 前台地址: http(s)://<IP>:<端口>/
- 浏览器: Chromium

> 任何复现此报告的人 **必须能用本节给出的 transport-shape 列表再现完全相同的请求路径**。
> 缺该列表的报告会被 docx 生成器拒绝。详见 `references/deployment-surface.md`。

### 5.2 复现步骤  *(MUST 每步骤一个截图,inline 嵌入)*

用人的语言描述每一步操作——做了什么、填了什么、点了什么。
不要出现"Playwright"、"自动化"、"AI"等字眼。
就当你在给同事口述复现过程。

**步骤 1**: <描述第一步操作>

访问 `<URL>`，<描述看到了什么>。

![截图说明](img/step1_xxx.png)

**步骤 2**: <描述第二步操作>

<描述做了什么>，<描述结果>。

![截图说明](img/step2_xxx.png)

### 5.3 结果验证

| 验证项 | 结果 |
|--------|------|
| <具体验证操作> | <实际结果，带数据> |

### 5.4 攻击链图示

```
攻击者 → 步骤1 → 步骤2 → ... → <漏洞类型> ✓
```

## 6. POC

```bash
# < POC 命令说明>
curl -s http://<target>/<endpoint>
```

## 7. 修复建议

<按优先级排列的具体修复方案，附代码示例>

## 8. 报送判定（文字反馈）

> **本节是 Skill 给使用者的建议，不是强制决策。最终由使用者决定。**

| 属性 | 值 |
|------|-----|
| **建议** | 📤 建议报送 / 📥 建议内部修复 |
| **实际危害** | 高 / 中 / 低 / 极低 |
| **修复优先级** | P0 / P1 / P2 / P3 |
| **理由** | <一句话核心判断> |
| **风险描述** | <此漏洞被利用后会怎样> |

**判定参考**:
- 📤 **建议报送**: 泄露真实凭证/Token/PII；越权访问；影响资金/数据完整性；可被自动化武器化
- 📥 **建议内部修复**: 仅泄露聚合统计/版本号；需社工/物理访问；配置类问题；影响仅限于 UI/体验

---

## Report Quality Rules

**禁止出现的词/句**:
- ❌ "Playwright 自动化" / "浏览器自动化" / "AI 辅助"
- ❌ "经过分析可以发现" / "值得注意的是" / "需要指出的是"
- ❌ "综上所述" / "总而言之" / "通过以上分析我们可以得出"
- ❌ "首先...其次...最后..." 的八股文结构
- ❌ 代码块里放大段注释解释每一行在做什么

**应该有的风格**:
- ✅ 直接说做了什么、看到了什么、结论是什么
- ✅ 截图对应操作步骤，图在文字描述下方
- ✅ 用 curl 命令写 POC，不用 Python 脚本包装
- ✅ 环境准备用简短的 bullet list，不用表格套表格
- ✅ 复现步骤用"访问 X，填入 Y，点击 Z，看到 W"的人话
- ✅ 结果验证用数据说话：`md5('VULN_VERIFY_001')` = `73680f3bc5749cbf9c1b4ebcde4f3a7e`，页面输出匹配
- ✅ 攻击链用简洁的 ASCII 流程图

---

## Screenshot Naming Convention

截图命名格式：`stepN_<描述>.png`

| 步骤 | 命名示例 | 说明 |
|------|----------|------|
| 访问首页 | `step1_access_homepage.png` | 初始状态截图 |
| 登录页面 | `step2_login_page.png` | 登录操作前 |
| 登录成功 | `step3_login_success.png` | 登录后状态 |
| 漏洞触发 | `step4_xss_payload.png` | 填写 payload |
| 结果确认 | `step5_result_confirm.png` | 执行结果 |

---

## Output Directory Structure

```
<project_root>/VULN-<NNN>-<short-title>/
├── report.md          # Full vulnerability report (simplified Chinese)
├── VULN-<NNN>_Report.docx    # Word document (ASCII name)
└── img/               # Screenshots
    ├── step1_access_homepage.png
    ├── step2_json_api_leak.png
    └── ...
```

---

## 强制前端校验清单(docx 生成器会逐条 grep — transport-agnostic)

```
[ ] ## 1. 至 ## 8. 八个节齐全
[ ] ## 5.1 节有 transport-shape 描述(任一即可):
    HTTPS  : "Connector / TLS / HTTPS / 终结点"
    HTTP   : "HTTP / 连接器 / 监听端口"
    WS/WSS : "WebSocket / ws:// / Upgrade"
    gRPC   : "gRPC / h2 / metadata / h2c"
    MQTT等 : "8883 / TLS / broker / 客户端 trust"
[ ] ## 5.2 节每个 "**步骤 N**" 后一行包含 ![](img/stepN_xxx.png) 截图
[ ] 实际文件 img/stepN_xxx.png 存在
[ ] ## 5.1 节有自证说明(mkcert / 自签 / trust-store / 代理段白名单 等,
    描述 chain 在 transport 上如何被端到端走通)
```
