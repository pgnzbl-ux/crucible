# Phase 3-5: Live Reproduction, Cleanup & Report Generation

## Phase 3: Live Reproduction

**Goal**: Determine whether the scanner's core security claim is observable through real HTTP behavior and capture proof.

### 3.0 Hypothesis-Driven Reproduction

1. Start from the supplied PoC, but do not assume its syntax, request shape, endpoint, encoding, or stated prerequisites are exact.
2. Correct reasonable defects and try variants that preserve the same core security claim.
3. Use source inspection, test accounts, raw HTTP, manual state, and browser automation to isolate where the chain succeeds or fails.
4. Treat those shortcuts as diagnostic evidence. End-to-end confirmation still requires the claimed security impact to be observed.
5. Record every material deviation from the supplied PoC and every prerequisite discovered during testing.
6. Do not silently broaden the claim. If the observed weakness or impact is narrower, classify it as partially confirmed or defense-in-depth.

### 3.1 Browser Automation (Playwright / chrome-devtools — REQUIRED for confirmation)

For findings whose claim depends on browser behavior, forms, client state, or any HTTP
response the reader can see: you **MUST** end up with at least one screenshot from a real
browser, not just curl output. Use the browser capability exposed by the current platform session (Playwright MCP or Chrome DevTools MCP); do not hardcode server-specific tool names. `curl` is for diagnostic isolation only.

Steps:

1. Navigate to the target via `page.goto(TARGET_URL)` (real navigation) or
   `chrome-devtools.navigate_page` with `type=url`. The address bar MUST show the real URL,
   including scheme + host + port (no `<input>` mockups).
2. Screenshot initial state — save under `img/step<N>_<verb>_<object>.png`.
3. Fill forms with payload values (if applicable).
4. Submit and capture the response.
5. Verify the exploit result — the screenshot must show status / body / visible artifact.
6. Screenshot the final state — same naming convention.

**Self-signed HTTPS gotcha:** Chromium-based browsers (including Playwright Chromium and
Chrome DevTools' Chromium) reject self-signed certs at the address bar by default. Two
acceptable workarounds, in order of preference:

- Use `mkcert` to create a local CA + cert that the OS / browser trusts.
- For Playwright: launch a `chromium` browser with `ignoreHTTPSErrors: true` (browserType
  option). For Chrome DevTools MCP: trust-on-first-use has been observed to work on
  Chromium 119+ — just navigate to the URL once and the cert is remembered for the session.

If neither works, do NOT fall back to `curl -k` screenshots: switch the cert to a real
chain (self-signed + manually add to Windows trust store via `certutil -addstore -f Root`)
or run the dev server with a publicly-trusted local DNS host (e.g. `localtest.me`).

**Hard rule:** "I tried the browser, it rejected self-signed" is not a valid reason to ship
a curl-only report. Fix the cert or downclass the verdict.

### 3.2 Evidence Authenticity Rules

> These rules are non-negotiable. Violations invalidate the entire report.

| Allowed | Forbidden |
|---------|-----------|
| `page.goto(TARGET_URL)` + `page.screenshot()` | `page.setContent()` + `page.screenshot()` |
| Overlay div with **real** API response data | Overlay div with fabricated data |
| Real alert via `page.on('dialog')` handler | Claiming "alert screenshot" when it shows the page, not the dialog |
| `page.evaluate()` calls `fetch()` against real endpoints | `page.evaluate()` returns hardcoded strings |

**Evidence roles**:

- **Diagnostic evidence** may use controlled accounts, manual headers/state, raw HTTP, or direct calls to determine which layer accepts or blocks the input.
- **Confirmation evidence** must demonstrate the core security impact through the execution path relevant to the claim.
- A diagnostic result does not automatically confirm the full finding. Verify the remaining prerequisites separately.
- If the supporting condition is real but the claimed impact is not demonstrated, classify the result as partially confirmed or defense-in-depth.

**Before adding each screenshot to the report, verify**:
- Shows real URL in address bar
- Shows target's favicon/logo or real page elements
- Not 100% fabricated HTML
- If overlay used, data comes from a real API call

**Alert limitation**: `alert()`, `confirm()`, `prompt()` are OS-level dialogs that
cannot be captured by `page.screenshot()`. Use DOM modification evidence for XSS —
see `references/xss_evidence_methods.md`.

### 3.3 Direct HTTP Testing (curl/fetch — Verification)

```javascript
// Use fetch inside Playwright evaluate for verification
await page.evaluate(async () => {
  const r = await fetch('/target');
  return await r.text();
});
```

### 3.4 Screenshot Naming and Placement

**Naming**: `step<N>_<action>_<object>.png` (e.g., `step1_access_homepage.png`)

**Placement**: Each screenshot goes immediately after the paragraph describing that step.
Do NOT collect all screenshots at the end.

**Ground-truth checklist (run before adding a screenshot to report.md)**:

1. Address bar shows the actual scheme + host + port that was targeted.
2. Status / response body is visible (HTTP code is in the rendered page or obvious from
   the captured response).
3. The page is from the real target domain (favicon, layout, or `<title>` matches).
4. If the screenshot is an overlay on a real page, the data inside the overlay came from
   a real API call (`fetch()` or `page.evaluate()` over the network), **not** a
   hard-coded string.
5. No `<input>` mockup, no `<iframe>` to fabricated HTML, no `page.setContent()` painting
   of fake evidence.

**Inline in report (non-negotiable)**: Every screenshot MUST be embedded in `report.md`
with markdown image syntax `![<alt>](img/<file>.png)` at the point it is discussed.
Referencing a screenshot only by text path (e.g. `> 截图：img/step1_x.png`) is
**forbidden** — if the reader cannot see the image inline at that location, it does not
count as evidence. The `md_to_docx.py` converter embeds `![]()` images into the `.docx`
in document order with captions, so inline syntax is also required for the Word report.

### 3.5 Payload Construction Rules

1. Check trailing character loss in SQL templates — end payload with `//` (comment)
2. Multiple code paths for same operation — check ALL paths
3. Cache/file write timing — use app's own delete via HTTP, not filesystem
4. Verify with hash comparison — use `md5('KNOWN_VALUE')` for RCE proof

### 3.6 Vulnerability-Type-Specific Reproduction

#### XSS Evidence Methods

> `alert()` cannot be screenshotted — it renders outside the browser viewport.

Acceptable evidence (in order of strength):
1. **DOM modification** — modify the page itself to show proof (RECOMMENDED)
2. **console.log + capture** — log proof, capture via `page.on('console')`
3. **Network out-of-band** — fetch to attacker-controlled endpoint
4. **Page state change** — redirect, change form action, modify links

For the complete guide, see `references/xss_evidence_methods.md`.

#### SQL Injection

> **SQLi confirmation = the injected input actually changes what DATA the response returns
> — NOT "server returned 500", "an SQL error leaked a fragment", or "the WAF log shows a
> hit".** Status codes, error-message fragments, and server logs only prove the code path
> is reachable; they are diagnostic, never confirmation. This is the exact analog of the
> SSRF rule ("500 ≠ SSRF confirmed").

**Confirmation standard:**

| Observation | What it proves | Allowed verdict |
|-------------|----------------|-----------------|
| Injected `UNION SELECT` puts a known marker (`md5('SQLI_PROOF')`) or real sensitive data (credentials/PII) into the **response body** | attacker-observable data exfiltration | CONFIRMED |
| Boolean/time-based blind: response content or timing changes deterministically with the injected condition, verified over multiple rounds | indirectly observable impact | CONFIRMED (blind) |
| SQL error message echoes a fragment near the injection point (`near '...'`) | only proves the input reached the SQL parser — reachability, not impact | diagnostic only, NOT confirmation |
| HTTP 500 / WAF log "检测到SQL注入" / app exception stacktrace | server-side signal | diagnostic only, NOT confirmation |
| A table name you control gets JOINed in and the query returns 200 | proves injection changes SQL structure; but if that table is empty / holds no sensitive data, **no impact is demonstrated** | code-reachable (upgrade to CONFIRMED only if sensitive data is actually returned) |

**Before constructing the payload — read every defense from code (white-box), do not
black-box-guess.** See `references/payload-bypass.md`. Specifically:
1. Read the **WAF / request filter** class: keyword list, symbol list, matching algorithm
   (substring? regex? case-sensitive? "keyword AND symbol both present"?), and the layer it
   runs on (request param? assembled SQL? both?). One read reveals the whole bypass frontier.
2. Read the **rendered SQL structure**: every `${}` placeholder, its clause position
   (SELECT list? WHERE? ORDER BY? ON?), whether the **same value is substituted into
   multiple positions**, and what's hard-coded after it (e.g. `ON x.link_id=...`).
3. Reason the payload to "survives every layer AND returns data" on paper BEFORE sending it.

**Structural blockers → Quick-Stop as FALSE POSITIVE (do NOT slide to "partially confirmed"):**
- Placeholder in a **SELECT list**: the value must be a column expression; no ON/JOIN/
  derived-table/UNION fits there.
- **Same value substituted into multiple positions** with conflicting needs (e.g. one
  position needs a bare table name for `x.*`, another needs a JOIN+ON).
- **ON clause hard-codes a column** the target table lacks (`ON x.link_id=...` but the
  table has no `link_id`) → Unknown column, can't JOIN it, can't read it.
- **Multi-line SQL** where `--`/`#` only truncate one line and `/*` has no closing `*/`
  (the hard-coded tail doesn't provide one) → can't truncate the remainder to fit UNION.
- **JDBC driver / connection pool** (e.g. Druid) strips inline `/**/` or rejects unclosed
  `/*` → a bypass that fools the request-layer WAF still dies at the driver.

Classic techniques (each must clear ALL layers above to count):
1. **Error-based**: `md5('SQLI_PROOF')` echoed in an error the app **returns to the client**
   (not merely logged server-side).
2. **Union-based**: `UNION SELECT md5('SQLI_PROOF')` → marker in response body.
3. **Boolean-based blind**: `AND 1=1` vs `AND 1=2` → response differs deterministically.
4. **Time-based blind**: `SLEEP(5)` → response time >5s, verified over multiple rounds.

#### SSRF

> **SSRF confirmation = the attacker-controlled side actually receives the request —
> NOT "the server returned 200/500".** A 500 / timeout / connection-refused is ambiguous:
> it can originate at backup-data generation, encryption, DNS, or the network, and
> proves nothing about whether the outbound request was ever sent. Only an observation
> made on the attacker's own server counts as confirmation.

**Mandatory preflight — verify the callback channel BEFORE triggering.**

SSRF is an out-of-band vulnerability. Before configuring any payload URL, prove the
target server can reach a host you control AND that you can observe the hit:

1. Stand up an attacker-side listener that records every request (method / path /
   `User-Agent` / source IP / body size) and exposes a view page (e.g. a small HTTP
   server with an `/inbox` route, or webhook.site / oast.fun / interactsh). Bind
   `0.0.0.0`, never `localhost`.
2. Confirm reachability **from inside the target's network context**: `docker exec` a
   `wget`/`curl` from the container to your listener and require a 200. On Docker
   Desktop use `host.docker.internal`; on a Linux bridge use the gateway IP; on a custom
   compose network confirm the actual gateway. **Never assume `127.0.0.1` or
   `172.17.0.1` works** — verify, or you will chase false negatives and misread them.
3. Only after the preflight listener returns 200, point the vulnerable setting
   (e.g. `webdav_url`, webhook URL, avatar URL) at your listener and trigger.

**Reproduction targets (only after the callback channel is confirmed reachable):**

1. `http://<your-listener>/` — primary proof: your side receives the server's request.
2. Internal addresses: `http://127.0.0.1:PORT`, `http://169.254.169.254/latest/meta-data/`
   (note: in containerized lab targets the metadata IP is usually unreachable → expect a
   500 / false negative; this does NOT disprove the SSRF primitive).
3. Internal port scan via response-time / status differential: 22, 3306, 6379, 8080.
4. `file:///etc/passwd`, `gopher://`, `dict://` if the client / protocol allows.

**Confirmation standard:**

| Observation | What it proves | Allowed verdict |
|-------------|----------------|-----------------|
| Attacker listener receives the request (real method/path, server's `User-Agent`, body) | SSRF primitive, confirmed end-to-end | CONFIRMED |
| Server returns 200 "success" but the listener saw nothing | Server *believes* it sent — not attacker-observable | diagnostic only, NOT confirmation |
| Server returns 500 / timeout / connection refused | Ambiguous (data-gen / encryption / network / the request itself) | NOT confirmation — fix the callback channel and retry |
| Config saved to DB, or a trace shows the sink was reached | Reachability of the vulnerable path | code-reachable (analysis input, not SSRF confirmation) |

If the payload URL targets an internal address you cannot listen on (e.g. real cloud
metadata), first confirm the SSRF primitive with your own listener, then argue the
internal-target impact from code + the confirmed primitive. Do not assert internal
impact from an ambiguous 500 alone.

#### IDOR / Access Control

1. **Horizontal**: Change user ID, access another user's data. **Must test with regular user accounts** — admin doing admin things is not IDOR.
2. **Vertical**: Access admin functions as regular user
3. **API**: Test endpoints with different tokens

#### File Upload

1. Extension bypass: `.php`, `.phtml`, `.htaccess`
2. Content-Type bypass: upload PHP with `Content-Type: image/jpeg`
3. Double extension: `shell.php.jpg`
4. Proof: upload, find URL, access, show execution result

#### Information Disclosure

1. JSON API leak: sensitive data in response
2. Visual overlay on real page with real data:
   ```javascript
   const data = await fetch('/api/leaked').then(r => r.json());
   const overlay = document.createElement('div');
   overlay.style.cssText = 'position:fixed;top:10px;right:10px;width:500px;background:white;padding:20px;z-index:99999;border:2px solid red;';
   overlay.innerHTML = `<h3>Real API Response</h3><pre>${JSON.stringify(data, null, 2)}</pre>`;
   document.body.appendChild(overlay);
   ```
3. Multi-endpoint verification
4. If only aggregate stats (counts) leaked → classify accordingly

### 3.7 Reproduction Iteration Loop

Max 5 rounds:

```
Round 1: Supplied PoC → OBSERVED/NOT OBSERVED
Round 2: Re-read code and isolate the failing layer → OBSERVED/NOT OBSERVED
Round 3: Correct reasonable syntax/encoding/request variants → OBSERVED/NOT OBSERVED
Round 4: Resolve runtime and environment conditions → OBSERVED/NOT OBSERVED
Round 5: Minimal end-to-end attempt → CLASSIFY AND MOVE ON
```

After max rounds, classify as:
- **Confirmed** — the core security impact is observed end-to-end
- **Partially confirmed** — a supporting weakness or narrower impact is proven
- **Defense-in-depth / code smell** — a best-practice gap exists without demonstrated security impact
- **False positive** — deterministic analysis or complete live testing proves the claim unreachable
- **Not reproduced / environment-dependent** — required runtime state could not be resolved

---

## Phase 4: Cleanup & Verification

For modifying vulnerabilities only. Information disclosure (read-only) → skip.

Cleanup via HTTP only:
1. Remove injected data via admin UI or HTTP delete
2. Clear cache via app's own function
3. Restore templates
4. Delete test accounts

---

## Phase 5: Report Generation

### 5.0 Evidence Gate (mandatory before writing report.md)

> Do NOT generate `report.md` until this gate passes. Writing a report on un-observed
> evidence is the single most common failure mode of this skill — do not skip it.

Answer these out loud, each tied to a concrete artifact (not an assertion):

1. **What is the single strongest piece of evidence for the "confirmed" claim?** Name it
   concretely: a request your listener received, a hash echoed in a response, a file
   accessed over HTTP, a DOM change on the real page.
2. **Is that evidence in the attacker's HTTP-observable range?** Server-side logs, DB
   rows, container stderr, and "the code reaches the sink" are NOT attacker-observable —
   they are diagnostic and cannot be the strongest evidence for a "confirmed" verdict.
3. **Does the verdict word match the evidence tier?** See SKILL.md
   "Verdict ↔ Evidence Tier Binding".
4. **Browser-screenshot requirement.** If the harm is rendered via HTTP response or DOM,
   report.md **MUST** contain at least one screenshot from a real browser (Playwright or
   chrome-devtools `navigate_page` + `take_screenshot`), named `step<N>_<action>.png`,
   embedded inline (`![<alt>](img/<file>.png)`). curl-only output is diagnostic, not
   confirmation. The only accepted exceptions: (a) the exploit produces no UI surface
   (e.g. server-side binary decoding, OOB callback that has no rendered page), or (b)
   the verdict is downclassed to "诊断性证据 / 已确认证据缺口" and the report says so.
5. **Deployment-shape declaration.** §5.1 of the report **MUST** explicitly list the
   connectors / ports that the tested target exposes, including which scheme (HTTP /
   HTTPS / both), which host and port, and where TLS is terminated (or that it is not).
   "环境跑起来了" without a connector list is not acceptable — anyone reproducing the
   report must be able to match the exact deployment shape. See
   `references/deployment-surface.md`.
6. **Section completeness.** report.md **MUST** contain all 8 numbered sections from the
   template (`references/report_template.md`). Missing sections cause the docx converter
   to fail loudly (see `scripts/md_to_docx.py`).

If any of (4)/(5)/(6) is unmet, `python scripts/md_to_docx.py ...` will refuse to write
the `.docx` — fix the markdown and rerun. If (1)/(2)/(3) yields "no", you may NOT write
"已确认 / CONFIRMED". Write the report with the matching lower verdict and state it
honestly. A partial-confirmation report is correct and useful; a false "confirmed" report
is the failure this gate exists to prevent.

### Output Structure

```
<project_root>/VULN-<NNN>-<short-title>/
├── report.md              # Full report (simplified Chinese)
├── VULN-<NNN>_Report.docx # Word document
└── img/                   # Screenshots: step<N>_<action>.png
```

### Report Template

See `references/report_template.md` for the full template.

### 报送判定

After the report, append:

```markdown
## 报送判定
**建议**: 📤 建议报送 / 📥 建议内部修复
**实际危害**: <高/中/低/极低>
**理由**: <一句话核心判断>
**修复优先级**: <P0/P1/P2/P3>
```

Criteria (non-binding — user decides):

**Suggest 报送** when:
- Leaks real credentials/tokens/PII
- Allows cross-user access/escalation
- Affects funds/permissions/data integrity
- Can be automated as a weapon (worm/watering-hole)

**Suggest 内部修复** when:
- Only leaks aggregate stats (count/avg)
- Only exposes version numbers/tech stack
- Requires social engineering/physical access
- Impact limited to UI/experience
- Configuration issues (CSP/HTTP headers)

**Core principle**: "actual harm > vulnerability type" — a CWE-200 info leak that
only exposes a count is still not a 报送 candidate. The skill outputs a suggestion;
the user makes the decision.

### Generation Command

```bash
由平台报告转换器读取 `report.md` 并生成 `.docx`
```

### md_to_docx Known Issues

| Trigger | Workaround |
|---------|-----------|
| Table contains `**bold**` | Avoid bold in tables |
| Long code blocks (>100 lines) | Split into multiple blocks |
| Chinese filename in output | Accept garbled filename, file is readable |
| Empty table cell | Fill with non-breaking space |
