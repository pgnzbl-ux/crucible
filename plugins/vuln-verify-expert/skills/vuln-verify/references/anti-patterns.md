# Anti-Patterns: Common Mistakes in Vulnerability Verification

## ❌ Anti-Pattern 1: "Code Confirmed" Without Exploitation

```
BAD: "The code uses fputs() to write user input, so RCE is confirmed."
GOOD: Actually create a PHP file via HTTP, access it, and show the md5 hash output.
```

## ❌ Anti-Pattern 2: Modifying Database to "Prove" Injection

```
BAD: "UPDATE table SET typename = 'malicious_code' — now access the page to see RCE."
GOOD: Inject through the application's own HTTP interface.
```

## ❌ Anti-Pattern 3: Ignoring Filters That Block Exploitation

```
BAD: "GetIP() uses X-Forwarded-For which is user-controlled, so SQL injection is possible."
GOOD: Check that preg_match("/[\d\.]{7,15}/") strips all non-numeric characters.
```

## ❌ Anti-Pattern 4: Claiming Default-Disabled Features Are Vulnerable

```
BAD: "The {dede:php} tag allows eval() — RCE confirmed!"
GOOD: Check that $cfg_disable_tags = 'php' disables the tag by default.
```

## ❌ Anti-Pattern 5: Fabricating Conditions

```
BAD: "If the attacker can modify the database directly, they can achieve RCE."
GOOD: This is NOT a vulnerability — it's a tautology.
```

## ❌ Anti-Pattern 6: Payload Ends with Trailing Quote Loss

```
BAD: Using payload "x';echo 'hello';$a='" — may lose trailing quote.
GOOD: Use "x';print(md5('PROOF'));//" — // comments out template remainder.
```

## ❌ Anti-Pattern 7: Fabricating Screenshot Evidence

```
BAD: page.setContent() to generate fake HTML, then screenshotting as "real target evidence"
GOOD: page.goto(REAL_URL) — every screenshot must show real page elements

BAD: Overlay div with hardcoded "XSS_PROOF" text claiming it's from the server
GOOD: Overlay div populated with data from real fetch() response

BAD: "Alert dialog screenshot" (impossible — alerts render outside browser viewport)
GOOD: DOM modification that visibly changes the page (banner, title, etc.)
```

## ❌ Anti-Pattern 8: Skipping Report Writing Due to 报送判定

```
BAD: "Unauthenticated API exposes aggregate counts → NO_REPORT, skip full report"
GOOD: Vulnerability confirmed → write full report, note '建议内部修复（仅泄露聚合数据）' at end
```

A confirmed vulnerability always gets a full report. The 报送 suggestion is
text feedback, not a decision to skip writing.

## ❌ Anti-Pattern 9: Testing with Admin Account for Non-Admin Claims
```
BAD: "As admin, I can create files under another user's username → IDOR confirmed!"
GOOD: Admin has full access by design. To test horizontal privilege escalation,
      create TWO regular users, login as user A, try to access/modify user B's data.
```

**Rule**: If the claim is about horizontal escalation (user A → user B's data) or
vertical escalation (user → admin), test with the **least-privileged account** that
should NOT have access. Admin doing admin things is normal design.

## ❌ Anti-Pattern 10: Skipping Type-Conversion Analysis Before HTTP Testing
```
BAD: "DTO has createTime marked hidden → it can be overwritten → try 5 different HTTP formats"
GOOD: First check: does the field have @DateTimeFormat (for @ModelAttribute)?
      Does the controller use @RequestBody (Jackson) or @ModelAttribute (Spring)?
      If @ModelAttribute and no @DateTimeFormat → LocalDateTime binding ALWAYS fails
      (ConversionFailedException). Stop here — no HTTP testing needed.
```

**Rule**: Before HTTP reproduction, trace the binding path:
- `@RequestBody` → Jackson → `@JsonFormat` applies
- `@ModelAttribute` → Spring DataBinder → `@DateTimeFormat` applies
- `@RequestParam` → Spring conversion → `@DateTimeFormat` applies
If the required annotation is missing for the path taken, the field CANNOT be bound
from an HTTP string. Stop.

## ❌ Anti-Pattern 11: Treating Disconnected Code Snippets as an Attack Chain
```
BAD: "step_1 shows ConsumerDTO.createTime, step_4 shows upsert with COLLECTION_NAME
      → ConsumerDTO → upsert of createTime"
GOOD: Trace the ACTUAL call chain. ConsumerDTO → UserServiceImpl.update (manual MyUpdate,
      no createTime). COLLECTION_NAME → ArticleDAOImpl.upsert (FileDocument, has
      uploadDate not createTime). DIFFERENT entities on DIFFERENT paths.
      No connection = no attack chain = false positive audit.
```

**Rule**: For each audit step, trace the exact code path end-to-end. If step_N's sink
doesn't match step_N+1's source, the audit fabricated the connection. Flag as disconnected.

## ❌ Anti-Pattern 12: Fighting Windows Path/Encoding Issues Repeatedly
```
BAD: curl → save to /tmp/result.json → python reads /tmp/result.json → FileNotFoundError
      → retry with different path → still broken → 5 rounds of path debugging
GOOD: On Windows Git Bash, /tmp/ doesn't map to Windows Python's path.
      Use project-relative paths (VULN-XXX/_data.json) for all intermediate files.
      For Python stdout encoding issues: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8').
      See references/windows-compatibility.md for the full guide.
```

**Rule**: On first Windows path/encoding failure, read `references/windows-compatibility.md`.
Don't retry with different path formats — use the documented workaround.

## ❌ Anti-Pattern 13: Treating the Scanner PoC as an Exact Specification

```
BAD: "The supplied request has the wrong endpoint or encoding → false positive."
GOOD: Extract the core security claim, reconstruct the real request path, and correct
      reasonable PoC defects before deciding whether the claim is reachable.
```

**Rule**: Scanner findings are hypotheses. A malformed or incomplete PoC is not a
deterministic blocker unless reasonable variants preserving the same core claim also fail.

## ❌ Anti-Pattern 14: Treating a Diagnostic Shortcut as Full Confirmation

```
BAD: Manually inject a prerequisite or protected state, call the sink directly,
     and report the complete attack chain as confirmed.
GOOD: Use the shortcut to isolate the relevant layer, state exactly what it proves,
      then verify the remaining prerequisites and impact separately.
```

**Rule**: Diagnostic tools may be stronger than the reported workflow. They are valid
for analysis, but final classification must reflect which parts of the core claim were
actually demonstrated.

## ❌ Anti-Pattern 15: Converting Runtime Uncertainty into a Static False Positive

```
BAD: "The required header, stored state, proxy behavior, or external service is unknown
     → the vulnerability does not exist."
GOOD: Classify the code path as runtime-dependent and resolve the condition during live
      verification. If it remains unresolved, report "not reproduced/environment-dependent."
```

**Rule**: Quick-Stop requires deterministic evidence. Unknown runtime state is a reason
to verify, not a reason to invent either a confirmation or a rejection.

## ❌ Anti-Pattern 16: Treating SQL Error-Message Leakage / HTTP 500 as Injection Confirmation

```
BAD: "Injecting tableName makes the server return 500 / leaks `near '.*,'` in the SQL
      error → SQL injection confirmed."
GOOD: SQLi is confirmed only when the injected input changes what DATA the response
      returns — a known marker (md5('SQLI_PROOF')) or real sensitive data in the body.
      Error fragments and 500s only prove the input reached the SQL parser (reachability),
      never impact. This is the SQLi analog of "500 ≠ SSRF confirmation".
```

**Rule**: Status codes, error-message echoes, and WAF/audit log hits are diagnostic. They
may accompany a confirmation but can never be the basis for "已确认". See the SQL Injection
confirmation table in `references/reproduction-guide.md`.

## ❌ Anti-Pattern 17: Black-Box-Probing Before Reading the Code Path and Defense Rules

```
BAD: Receive a SQLi claim → immediately curl different payloads against the live target,
     probing one keyword/symbol at a time to "see what the WAF blocks".
GOOD: First read the full code chain (entry → sink → every defense) and extract the WAF's
      actual rule (keyword list, symbol list, matching algorithm, layer) from its class —
      often in a dependency jar. One read reveals the whole bypass frontier. Construct the
      payload on paper to "survives every layer", THEN confirm via a single HTTP request.
```

**Rule**: White-box first, live target only for final confirmation. The code already tells
you the answer; black-box-probe only what the code cannot resolve (runtime state). See the
Phase 2 Exit Checklist in SKILL.md and `references/payload-bypass.md`.

## ❌ Anti-Pattern 18: Sliding to "Partially Confirmed" When No Exploitable Payload Exists

```
BAD: "I can't construct a payload that exfiltrates data (the SQL structure blocks every
      bypass), but the input does reach the SQL and the WAF is bypassable → partially
      confirmed."
GOOD: If every bypass is exhausted and NO payload can return data (structural blocker:
      placeholder in SELECT list, same value in conflicting positions, ON hard-coding a
      missing column, multi-line SQL with no possible truncation), it is a FALSE POSITIVE.
      Note any real code weakness (non-parameterized concat, weak WAF) as defense-in-depth,
      but do not inflate the verdict.
```

**Rule**: Reachability + bypassable WAF ≠ exploitable vulnerability. If no payload produces
attacker-observable impact, the verdict is false positive (with a defense-in-depth note),
not "partially confirmed". Do not hedge just to avoid a negative result.
