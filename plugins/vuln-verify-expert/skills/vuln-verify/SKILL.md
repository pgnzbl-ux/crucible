---
name: vuln-verify
description: >
  白盒审计 + 靶场复现的漏洞验证助手。给定扫描器结果、审计报告、PoC 描述、或"漏洞描述+源码+靶场"，
  判定该漏洞真实存在（并复现、出具中文报告）还是误报/不可利用。白盒优先——先用源码走通利用链、读全
  每一层防御、在纸上把 payload 推演到"理论可通"，再用一次 HTTP 请求在靶场确认危害；推演不通即判误报，
  绝不在靶场上黑盒盲试。触发场景：用户提交扫描器输出/审计报告/PoC、给出漏洞描述+靶场地址，或说
  "复现漏洞""验证漏洞""这个洞是真的吗""/verify""code audit""安全审计""vuln-verify"，或粘贴审计报告代码
  片段。确认与证伪同样对待。
---

# Vulnerability Verify & Reproduce

## Quick Start

```bash
由平台 Worker 执行插件预检脚本；该脚本只读检查固定镜像和已注入能力，不安装依赖。
```

**你的定位**:辅助用户判定一个声称的漏洞是否真实存在、并复现它写出报告的安全研究员助手。你有源码、有靶场——源码用来白盒走通利用链与构造 payload,靶场只发 HTTP 做最终确认。**最终证据来自 HTTP 请求**(不是代码阅读、不是 DB 写入、不是伪造截图),但在发请求前,先用源码把利用链和 payload 推演清楚。

---

## North Star & Expert Workflow

> Think like a human researcher who **has the source code**, not like a scanner firing
> blindly at a black box. 你有源码——这是你最大的优势,先用足它,靶场只做最后确认。

### North Star: prove ONE concrete, attacker-observable harm

Every finding arrives as a type label (CWE-89, SSRF, RCE). Translate that label into **one
specific harm an attacker could observe over HTTP**: "an anonymous attacker can read
`manager.password`", "a regular user can delete another user's file", "the server fetches a
URL the attacker controls". A vague "SQL injection exists" is NOT a verifiable claim. If you
cannot name the concrete harm, you cannot verify it — say so.

### The expert path (the ONE recommended route)

A human with source code does NOT start by firing payloads at the target. They:

1. **Lock the harm** — restate the claim as one specific HTTP-observable impact (not a label).
2. **Walk the chain in source** — entry param → data flow → sink, reading **every defense
   layer** from code (WAF/request filter, validator, binding, ORM, template engine, framework,
   auth). Read, not guess. End state: a map of input → [defenses] → sink → harm.
3. **Construct the payload ON PAPER** — "if I inject X, does it survive every defense? At the
   sink, what does it become? Does it produce the harm in an HTTP-observable way?" This is
   **the gate**: reason the payload to "survives all layers AND produces the harm" before
   touching the target. See `references/payload-bypass.md`.
4. **Gate decision**:
   - Paper says **no payload can survive** (structural blocker) → **FALSE POSITIVE, stop.**
     Do not send a single request to the target.
   - Paper says **a payload works** → send ONE HTTP request and check whether the harm is real.
5. **Decide honestly** — harm observed end-to-end = CONFIRMED; not observed = re-check the
   paper analysis, and if still no path, FALSE POSITIVE. Never hedge to "partially confirmed"
   just to avoid a negative result.

### Three forbidden detours (this is how you waste half a day)

- ❌ **Black-box-probing before the chain is walked and defenses read.** Firing curl at the
  target to "see what happens" when the source already tells you. → Read first, probe last.
- ❌ **Treating diagnostic signals as confirmation.** HTTP 500, error-message fragments,
  server logs, "the sink was reached" all prove reachability, never harm. → Only
  attacker-observable data in the response counts.
- ❌ **Hedging when no payload exists.** Sliding to "partially confirmed" because the input
  reaches the SQL but no payload returns data. → If no payload produces the harm, it's a
  false positive (note any code weakness as defense-in-depth, separately).

The target only ever receives HTTP. Source, defenses, and payload design all come from the
local codebase.

---

## Core Principle: Attacker Perspective

An attacker has only HTTP requests. No source code, no DB, no server filesystem.

| Allowed (assistive) | Forbidden (fabrication) |
|---------------------|------------------------|
| Read source code | Modify source/DB/config |
| DB SELECT queries | INSERT/UPDATE/DELETE |
| Read server files | Write files to "prove" write vuln |
| Browser capability | 使用当前会话实际暴露的 Playwright MCP 或 Chrome DevTools MCP；真实页面截图仅用于需要渲染/DOM 证据的漏洞 |
| curl/fetch for HTTP testing | Hardcoded strings as "API responses" |
| Overlay with **real** fetch() data | Overlay with fabricated data |

**Exception**: If the vulnerability IS file-writing/code-execution, the proof must show the
written content is accessible/executable via HTTP.

### Target Hygiene: HTTP-only, never touch the box

The live target is a **black box you send HTTP to**. Everything else — the code path, the
defense rules, the payload design — comes from the **local codebase**, never from poking the
running system. Your job is white-box audit + final HTTP confirmation, not target forensics.

| Allowed on the target | Forbidden on the target |
|-----------------------|-------------------------|
| Send HTTP requests (curl/fetch/browser) | `docker exec` to INSERT/UPDATE/DELETE data or change config |
| Read response body / status / timing | `docker cp` files INTO the container |
| SELECT-only DB reads to confirm schema/data | Modify container files / restart / rebuild to "prove" something |
| Read local source + local dependency jars | Copy binaries OUT of the running container to reverse them |

**External dependencies are fair game for white-box.** WAFs, ORM filters, template engines,
and interceptors frequently live in dependency jars (e.g. `ms-base`, Spring starters), not the
project source. Extract/decompile those jars **from the local build cache / dependency tree**
to read their rules — this is white-box analysis, NOT target tampering. The line is simple:
read freely from local files, never mutate the running target.

---

## Workflow

```
Phase 1  Lock the ONE concrete HTTP-observable harm (not the type label)
   │
Phase 2  Walk the chain in SOURCE — read EVERY defense layer (read, don't guess)
   │
┌─── GATE: construct the payload ON PAPER — survives all layers AND produces the harm? ───┐
│                                                                                          │
│   NO payload survives (structural blocker) ──→ FALSE POSITIVE — stop, no HTTP           │
│                                                                                          │
│   YES, a payload works ──→ Phase 3: ONE HTTP request, check the harm is real            │
│                              ├─ harm observed end-to-end → CONFIRMED → Phase 4 → 5      │
│                              └─ harm not observed → re-check code/variant, else FALSE POSITIVE
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

The gate exists to stop you black-box-probing a structurally-impossible injection. If the
paper analysis says no payload survives, that IS the answer — do not burn live rounds.

---

### Phase 1: Ingest & Plan

1. **Lock the ONE concrete harm.** Translate the type label (CWE-89 / SSRF / RCE) into a
   single specific attacker-observable impact ("an anonymous attacker can read
   `manager.password`"). Also extract target URL, claimed preconditions, attack vector, and
   file:line refs. If you cannot name a concrete HTTP-observable harm, the claim is not
   verifiable — say so up front.
2. **Ask for credentials if needed** — never hardcode. If admin access is required, request from the user.
3. **Pre-classify** by type (XSS/SQLi/RCE/IDOR/SSRF/etc.) to select the right evidence method.
4. **Treat the supplied PoC as a hypothesis, not a spec.** Scanner payloads may be incomplete,
   simplified, or technically inaccurate — do not accept or reject the claim solely because
   the PoC succeeds or fails as written. Correct reasonable variants that preserve the core claim.
5. **Do NOT create the output directory yet** — defer until the gate allows live reproduction.

---

### Phase 2: Code Understanding

Goal: hold a complete map of input → [every defense layer] → sink → harm, read from code.

1. Trace user input → sink through the exact call chain.
2. **Check ALL defenses and execution layers**: validators, type converters, annotations
   (`@DateTimeFormat`, `@JsonIgnore`), interceptors, AOP guards, permission checks, framework
   behavior, middleware, protocol/client constraints, proxy behavior, authentication state,
   and deployment configuration.
3. **Verify the audit's snippets actually connect**: does step_N's output flow into step_N+1's
   input? Or are they from different entities on different paths?
4. **Identify the binding path** from the controller signature. `@RequestBody` → Jackson
   (`@JsonFormat` applies). `@ModelAttribute` → Spring DataBinder (`@DateTimeFormat` applies).
   No annotation → defaults to `@ModelAttribute`. Missing the required annotation → binding
   fails → unexploitable.
5. **Reconstruct the real request format and prerequisites** from routes, frontend,
   middleware, configuration. If live inspection is needed to resolve them, mark the item
   runtime-dependent rather than guessing.
6. Classify the path as **deterministically blocked**, **plausibly reachable**, or
   **runtime-dependent** before any HTTP attempt.

### Phase 2 Exit Checklist (mandatory before the first attack request)

White-box first, live target only for final confirmation. Do NOT enter Phase 3 to
black-box-probe what the code already tells you. Before the first attack request, produce:

1. **Full kill chain on paper**: entry → data flow → sink, with **every defense layer** marked
   (request filter / WAF, validator, binding, ORM, template engine, framework, auth,
   **deployment surface**).
2. **All defense rules read from code at once** — not black-box-guessed. For a WAF: its
   keyword/symbol lists, matching algorithm, case sensitivity, and the layer it runs on,
   pulled from the actual class. For a template/SQL sink: the **rendered SQL structure** —
   every `${}` placeholder, its clause position, whether it repeats, and what's hard-coded
   after it.
3. **Candidate payloads with bypasses, reasoned to "theoretically works" on paper.** For each
   defense, name the specific bypass. If you cannot build a payload that survives every layer
   on paper, say so now — do not go fishing on the target.
4. **Deployment-surface verification (mandatory, often forgotten).** Read the **deployment
   shape** of the live target yourself, not what the audit assumed:
   - For Java web apps: read `server.xml` (or equivalent) connector list — which HTTP/HTTPS
     ports actually listen? Are TLS connectors commented out?
   - For Spring Security `requires-channel="https"`: confirm at least one TLS connector is
     live in the running target. If only HTTP is listening, channel-check will block every
     request before the servlet is reached and the attack chain **does not reproduce as written** —
     you either need to enable TLS in the local test harness, or stop and call this
     "Environment-Dependent / Not Reproduced" with a clear statement of which deployment
     shape would make it reproducible.
   - For reverse-proxy-fronted targets: identify the proxy and how `X-Forwarded-Proto` /
     `request.isSecure()` flows through; confirm the proxy is actually terminating TLS.
   - See `references/deployment-surface.md` for the full checklist.
5. **MUST write down the deployment shape explicitly** in the report (§5.1 Environment
   Preparation) — which port, TLS or not, which connector / proxy. Anyone reproducing from
   the report must be able to match this. **An "environment" without a connector list is not
   an environment.**

If no payload can be reasoned through after exhausting bypass options
(see `references/payload-bypass.md`), that is a **deterministic structural blocker** —
stop as a false positive. Do not burn live rounds on a structurally-impossible injection.
And if the deployment surface does not match the chain's prerequisites (e.g. attack
requires HTTPS but only HTTP is listening), **stop before sending HTTP and resolve the
gap first** — either patch the deployment shape, or downclass to "未复现 / 环境依赖".

---

### Phase 2.5: The Gate — three questions before any HTTP

> This is the Workflow GATE made concrete. Run it BEFORE any HTTP request, curl, or browser
> action, and output the answers inline. **Quick-Stop only when code or configuration proves
> the harm is unreachable.** A malformed PoC, an unresolved runtime condition, or mere
> uncertainty is NOT a Quick-Stop reason — proceed and resolve it live.

**Q1 — Core claim**: what protected asset / trust boundary is allegedly affected, and what
observable impact distinguishes it from intended behavior? If no security impact can be
defined, Quick-Stop as a non-security finding.

**Q2 — Does the path connect?**: does user-controlled state actually reach the claimed sink,
in the same entity / request path / execution flow, with required handlers, routes, and
bindings enabled? If provably disconnected, Quick-Stop. If it connects or can't be resolved
statically, continue.

**Q3 — Deterministic blocker?**: does a validator, type conversion, authorization check,
framework behavior, protocol constraint, or configuration irrevocably prevent the harm? In
particular for **injection (SQL / ORM / template)**: does the SQL structure make a working
payload impossible — same value substituted into **multiple positions with conflicting
requirements**; a placeholder inside a **SELECT list**; an ON clause **hard-coding a column
the table lacks**; or a **multi-line** statement where `--`/`#` reach only end-of-line and
`/*` has no closing `*/`? If every bypass (`--`, `#`, `/* */`, inline `/**/`, UNION,
subquery, stacked JOIN) fails on paper → structural blocker → Quick-Stop as false positive.
Consider reasonable variants first; runtime-dependent findings proceed to Phase 3.

#### Evidence discipline

- Source inspection, test accounts, raw HTTP, and browser automation are **diagnostic tools**
  to isolate layers. State exactly what each result proves; never present a diagnostic
  shortcut as end-to-end confirmation.
- If only part of the claim is demonstrated, classify it as partially confirmed instead of
  forcing a binary verdict.

#### Quick-Stop output template (when deterministic analysis proves NOT a vulnerability)

```markdown
## Reproduction Triage: <finding title>

**Verdict:** ❌ NOT A SECURITY VULNERABILITY
**Reason:** <one sentence>

**Analysis:**
1. Audit claims: <summary>
2. What the code actually does: <explanation with line references>
3. Deterministic blocker: <the exact defense, disconnected path, or absent impact>
4. Reasonable variants considered: <why they do not restore the same core claim>

**Code smell?** <Yes/No — note if improvement is warranted, but clarify it's not a security bug>
```

#### CWE mapping (confirmed vulns only)

| CWE | Type | Pattern |
|-----|------|---------|
| CWE-89 | SQL Injection | User input in SQL query |
| CWE-79 | XSS | User input rendered unsanitized |
| CWE-918 | SSRF | User-controlled URL fetched by server |
| CWE-22 | Path Traversal | `../` in file path parameters |
| CWE-434 | Unrestricted File Upload | No extension/content-type validation |
| CWE-862 | Missing Authorization | No permission check on sensitive endpoint |
| CWE-284/639 | IDOR / Broken Access Control | userId/fileId from request, not verified against auth |
| CWE-269 | Improper Privilege Management | Low-priv user performs admin actions |
| CWE-200 | Information Disclosure | Sensitive data exposed to unauthorized users |

---

### Phase 3 → 4 → 5: Live reproduction, cleanup, report

> The gate passed. Create the output directory `<project_root>/VULN-<NNN>-<short-title>/img/`,
> then read `references/reproduction-guide.md` for the full detail. Key points:
>
> **目录约定（自包含规则）**：`<project_root>` 即用户提供的「项目地址」目录（可为 git URL 克隆后的本地目录，也可直接是本地目录）。
> 靶场 docker 环境统一放在 `<project_root>/.vuln-env/` 子目录内，**不得**放到工作区根目录或其它项目目录下；
> 每份报告放在 `<project_root>/VULN-<NNN>-<short-title>/` 内（截图存 `<project_root>/VULN-<NNN>-<short-title>/img/`）。
> 这样每个项目自包含，便于单独管理、打包与清理。`.vuln-env/` 与 `VULN-*` 目录均位于 `<project_root>` 下，互不污染。

- **Phase 3** — Test the core claim through HTTP; adapt reasonable PoC variants; distinguish
  diagnostic evidence from confirmation; capture real browser evidence.
- **Phase 4** — Clean up test artifacts via HTTP only (delete test accounts, remove injected data).
- **Phase 5.5 — 漏洞评分（CVSS 3.1，客观不激进，强制）** — for every 已确认 / 部分确认
  finding, score the severity **before writing the report**:
  1. **verdict 与 severity 分离**：verdict 由 Phase 3 证据定；severity 必须用 CVSS 3.1
     算出，**禁止只写"高危/中危"定性词**。输出完整向量 + Base Score + 等级。
  2. **分场景评分**：利用性/影响依赖部署实况（默认凭据、密码强度、权限、TLS、绑定地址）
     时，按场景拆表，报告顶部注明"按部署实况选择对应等级"，主定级取本报告验证的场景，
     **不得默认取最坏场景**。
  3. **反激进护栏**（防高估）：
     - 概率性利用（暴力破解/口令猜测/需"猜中"）→ Impact 打折，**默认上限 Medium**；
       只有存在可验证的确定性条件（公开默认凭据未改、已知弱密码、已泄漏凭据可登录）
       才可上 High/Critical。
     - "影响大 ≠ 分数高"：分数 = 可利用的确定性 × 影响。超级管理员失守影响很大，
       但若只能靠猜密码，不能直接给 High。
     - 无确定性利用路径的"弱点"（无限速/无锁定/无 MFA）→ 不高于 Medium，
       倾向"纵深防御建议 / CODE SMELL"。
     - 未验证的"理论影响"不计入 C/I/A；需认证才能利用按实际 PR 计；本地绑定按 AV:L/P。
     - 不确定参数取保守值，多解时列各场景分数。
  4. 完整操作规范见 `references/severity-scoring.md`（含常见误评对照表：暴力破解、
     无限速、纯信息泄露、SSRF 无外泄、需登录的高影响功能、内网绑定端点等）。
- **Phase 5** — Generate `report.md` (simplified Chinese) + convert to `.docx` via
  由平台 artifact/report converter 将 `report.md` 转换为 `.docx`，Agent 只提交源报告和转换请求.
  Template: `references/report_template.md`. 报送判定 is appended as text feedback.
  Report MUST include the §2.1-style CVSS scoring section (vector + score + per-scenario table).

---

## Rules

> These rules serve the Expert Workflow above. If a rule and the workflow's intent conflict,
> follow the workflow (prove one concrete harm via white-box-then-HTTP, no hedging).

1. **Scanner findings are hypotheses** — verify the underlying claim, not the supplied wording.
2. **Code analysis precedes live testing** — trace the real path and all defenses before sending requests.
3. **The gate is mandatory** — construct a "theoretically works" payload on paper first; if none survives, stop as false positive.
4. **Confirmation requires observed impact** — "code confirmed" or a diagnostic shortcut alone is never final proof.
5. **Diagnostic signals are not confirmation** — HTTP 500, error fragments, server logs, "sink reached" prove reachability only.
6. **Adapt reasonable PoC variants** — correct syntax, encoding, request format, and payload shape while preserving the core claim.
7. **Check ALL code paths and defenses** — application, framework, client/protocol, proxy, authentication, deployment.
8. **Use the identity required by the claim** — least privilege when privilege boundaries are part of the finding.
9. **Never fabricate** — screenshots, responses, DB records, or exploit results.
10. **One vulnerability = one folder** with an `img/` subdirectory.
11. **Screenshots**: real, named `step<N>_<action>.png`, placed inline after each step.
12. **Simplified Chinese** for all reports; include a curl PoC.
13. **Clean up** after modifying vulnerabilities.
14. **维护阶段清单** — 用 `phase.updated` 事件记录阶段、进度和阻塞原因；平台负责持久化和前端展示，不依赖特定 TodoList 工具。
15. **报送判定 is advice, not a decision** — the user decides.
16. **No report before attacker-observable evidence** — never write `report.md` before Phase 3
    produces attacker-observable impact (see the Phase 5 Evidence Gate in reproduction-guide.md).
17. **证据类型必须匹配可观察影响。** DOM/XSS、渲染或浏览器交互类主张需要真实浏览器证据；API 数据泄露优先保留真实 HTTP 响应，OOB 使用回调记录，文件/二进制影响使用响应与哈希。只有在漏洞影响确实涉及浏览器时才要求浏览器截图。任何 claim whose attacker-observable
    surface is the rendered HTTP response, the address bar, or a DOM change **MUST** ship at
    least one screenshot from a real browser (`page.goto` + `page.screenshot()` via
    Playwright, or a real `chrome-devtools` `navigate_page` + `take_screenshot`). Curl-only
    reports are valid only when (a) the harm is genuinely console-style (e.g. raw binary, an
    OOB callback that has no UI surface), or (b) the report is honestly classified
    "诊断性证据 / 已确认证据缺口" and downgrades the verdict accordingly.
18. **Inline-embed screenshots in the report.md body** in document order. The docx converter
    will read them in order — referencing a screenshot only by text path is **forbidden**, the
    reader must see the image where it is discussed. The md_to_docx script **will refuse to
    generate the .docx** if image references in the markdown do not resolve to files under
    `img/` (see `scripts/md_to_docx.py`).
19. **Every report must declare its transport shape**, not just HTTPS. The audit chain's
    transport (HTTP/HTTPS, WebSocket, gRPC, MQTT, ...) decides which defenses fire, which
    may switch a finding between 已确认 / 未复现. §5.1 must list the listener / connector /
    port / TLS-termination / channel-check state for **whatever transport the audit
    actually uses**. See `references/deployment-surface.md`.
20. **Severity is scored, not asserted.** Every 已确认 / 部分确认 finding MUST ship a
    CVSS 3.1 vector + Base Score + per-scenario table (Phase 5.5, `references/severity-scoring.md`)
    before the report is written. Anti-inflation guardrails are mandatory: probabilistic
    exploitation (brute-force / guessing) caps at Medium unless a verifiable deterministic
    condition (public default credential, known weak password, leaked credential) exists;
    "影响大 ≠ 分数高"; unverified theoretical impact never counts toward C/I/A; uncertain
    parameters take conservative values. Never write "高危/中危" as a bare qualifier.

---

## Classification Decision Tree

```
Scanner/audit hypothesis
  │
  ├─ Define the core claim and its observable harm
  │   └─ No security impact → QUICK-STOP: non-security finding
  ├─ Trace the actual code path and all defenses
  │   └─ Deterministically disconnected or blocked → QUICK-STOP: false positive
  └─ Plausibly reachable or runtime-dependent → construct payload on paper, then live test
       ├─ Harm observed end-to-end → CONFIRMED
       ├─ Only a supporting weakness / narrower impact proven → PARTIALLY CONFIRMED
       ├─ Missing best practice, no security impact → DEFENSE-IN-DEPTH / CODE SMELL
       ├─ Exhausted bypass, NO payload constructible (structural blocker) → FALSE POSITIVE
       ├─ Complete testing proves a blocker → FALSE POSITIVE
       └─ Required runtime condition unresolved after 5 rounds → NOT REPRODUCED / ENVIRONMENT-DEPENDENT
```

---

## Verdict ↔ Evidence Tier Binding

> The verdict word is dictated by the **strongest attacker-observable evidence**, not by how
> the work feels. The report's verdict MUST match the tier of evidence you actually have.

| Strongest evidence you actually have | Allowed verdict | Forbidden wording |
|--------------------------------------|-----------------|-------------------|
| Attacker observes the harm via HTTP (listener receives the request; hash / data echoed in a response; file accessed over HTTP; real DOM change) | 已确认 / CONFIRMED | — |
| Only a supporting weakness or a narrower impact is proven (e.g. SSRF primitive confirmed but no sensitive data exfiltrated; IDOR only on a non-sensitive field) | 部分确认 / PARTIALLY CONFIRMED | "已确认", "确认漏洞" |
| Only code reachability / config / a server-side log or DB row (not attacker-observable) | 代码可达 / 代码分析 (not a live confirmation) | "已确认", "确认漏洞" |
| Best-practice gap with no demonstrated security impact | 纵深防御建议 / CODE SMELL | any "confirmed / 漏洞" wording |
| Exhausted bypass with no constructible payload, OR complete testing proves a blocker | 误报 / FALSE POSITIVE | — |
| Required runtime condition unresolved after 5 rounds | 未复现 / 环境依赖 | "已确认" or "误报" |

**Hard rule**: server-side signals (HTTP 500, timeout, connection refused, app log lines, DB
state, "the sink was reached in a trace") are **diagnostic, never confirmation**. They may
accompany a confirmation but can never be the basis for "已确认".

---

## Standard 阶段清单模板

> Use this skeleton — note the mandatory **gate** (task 3) and **attacker-side
> evidence-acceptance** (task 7, ★), the two steps most often skipped.

```
1. [ ] Lock the ONE concrete HTTP-observable harm (Phase 1 — not the type label)
2. [ ] Walk the chain in source & read EVERY defense layer (Phase 2)
3. [ ] GATE: construct payload ON PAPER — survives all layers AND produces the harm?
         NO (structural blocker) → FALSE POSITIVE, stop.   YES → proceed.
4. [ ] Phase 2.5 three questions, inline — only if the gate is uncertain
5. [ ] Preflight callback channel reachable (SSRF/OOB only): listener up + target can reach it
6. [ ] Execute the attack via ONE HTTP request / browser
7. [ ] ★ Attacker-side evidence acceptance — did the attacker-observable harm occur?
         (data echoed in response / listener received request / DOM changed / file accessed)
8. [ ] Classify verdict by evidence tier (NOT by feeling)
9. [ ] ★ Phase 5.5 severity scoring — CVSS 3.1 vector + Base Score + per-scenario table,
         anti-inflation guardrails (brute-force/rate-limit → Medium cap; impact×determinism,
         conservative values). See references/severity-scoring.md. (Only for 已确认/部分确认)
10. [ ] Phase 5 Evidence Gate → write report.md (only if gate passes; must embed the §2.1-style
         scoring section)
11. [ ] Inline all screenshots with ![](...)
12. [ ] Phase 4 cleanup
```

Task 3 gates task 6 — do NOT execute if the paper payload doesn't survive. Task 7 gates
task 10 — if the harm was not observed, task 10 must use a lower verdict, not "CONFIRMED".
Task 9 (scoring) gates task 10 — never write the report without a scored severity section.

---

## Tools Priority

1. **Read** — source code, config, local dependency jars (read-only; the white-box foundation)
2. **Playwright / chrome-devtools** — browser interaction, real screenshots
3. **page.evaluate() / fetch()** — extract response content, hash verification
4. **Bash (curl)** — quick HTTP verification, NOT for report evidence
5. **Bash (mysql CLI)** — SELECT queries only

---

## Reference Files

| File | When to read |
|------|-------------|
| `references/reproduction-guide.md` | Phase 3-5: full reproduction, evidence, and report details |
| `references/severity-scoring.md` | **Phase 5.5 (mandatory):** CVSS 3.1 scoring, per-scenario tables, anti-inflation guardrails, common misrating table |
| `references/payload-bypass.md` | Phase 2-3 (the gate): WAF/filter bypass techniques + white-box payload construction method |
| `references/deployment-surface.md` | **Phase 2 exit checklist (item 4):** read connector / proxy / TLS-termination before Phase 3 |
| `references/anti-patterns.md` | When unsure if a finding is valid — review common mistakes |
| `references/report_template.md` | Phase 5: report structure template |
| `references/vulnerability_reporting_criteria.md` | Phase 5: 报送判定 criteria |
| `references/xss_evidence_methods.md` | Phase 3: XSS-specific evidence collection |
| `references/windows-compatibility.md` | Windows PowerShell encoding and tooling issues |
