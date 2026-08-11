# WAF / Filter Bypass Techniques & White-Box Payload Construction

Use this when a defense layer (request filter / WAF / ORM wall / template sink) sits
between user input and the sink. Read it BEFORE constructing payloads, at the Workflow GATE.

## Golden rule: white-box the defense, do not black-box-probe it

A WAF's behavior is fully knowable from its code. Before sending a single probe:

1. Locate the filter class — often in a **dependency jar** (`ms-base`, a Spring starter,
   a Shiro interceptor). Extract/decompile it from the **local** build cache or dependency
   tree, never from the running container.
2. Read its **keyword list**, **symbol list**, **matching algorithm**, **case sensitivity**,
   and **the layer it runs on** (request parameter values? the assembled SQL string? both?).
3. Write down exactly which combinations trigger a block. Most homegrown WAFs use a rule
   like *"keyword AND symbol both present"* (e.g. `SqlInjectionUtil`: block only when a
   `\b(keyword)\b` AND a symbol like `(`, `--`, `/* */` co-occur, case-insensitive). Once
   you know the rule, the bypass is mechanical.

Black-box-probing one keyword at a time burns rounds and misses the rule. One code read
tells you the whole frontier.

## Classic bypass techniques (map each to the specific rule it defeats)

| Technique | Example | Defeats |
|---|---|---|
| Inline comment splitting | `SEL/**/ECT`, `UN/**/ION` | substring/regex keyword match needing the literal contiguous keyword |
| Case variation | `UnIoN SeLeCt` | case-sensitive match only (NOT `containsAnyIgnoreCase`) |
| Block comment truncation | `payload/*` | — but needs a closing `*/`; unclosed `/*` often dies at the JDBC driver (Druid) |
| Line comment truncation | `payload-- ` / `payload#` | truncates to end-of-line only — useless on multi-line SQL |
| MySQL versioned comment | `/*!50000UNION*/` | content executes in MySQL but some WAFs mis-parse it |
| Encoding | URL / hex / unicode | WAFs that scan raw bytes instead of the decoded value |
| Off-listword payload | pure table name; `UNION SELECT` when `union`/`from` aren't on the keyword list | keyword blocklists that omit structural words |

**Critical: a bypass must clear EVERY layer.** A payload that fools the request-layer WAF
can still die at the JDBC driver (Druid strips `/**/`, rejects unclosed `/*`) or at the SQL
parser itself. Reason through all layers on paper before going live.

## White-box payload construction method (the GATE step)

For each injection point, answer these **on paper** before any HTTP attempt:

1. **Where does my value land?** Identify every clause position the value is spliced into
   (SELECT list, FROM, JOIN, ON, WHERE, ORDER BY, LIMIT). One parameter can appear in
   **multiple positions** with the same value.
2. **What does each position require?**
   - SELECT list → column expression only (no ON/JOIN/FROM/UNION/derived-table)
   - `ON x.col` → `x` must be a bare identifier whose table has column `col`
   - WHERE value → can carry UNION (UNION connects two SELECTs across a WHERE)
   - ORDER BY → cannot carry UNION (UNION binds SELECTs; ORDER BY sits at statement end)
3. **Do the positions conflict?** If the same value must be BOTH a bare table name (for
   `x.*`) AND a JOIN+ON expression, no single string satisfies both → **structural blocker**.
4. **Can I truncate the remainder?** To fit UNION you usually must comment out everything
   after your injection point. Check: single-line SQL (`--`/`#` reach end-of-statement) or
   multi-line (need `/* ... */`, and the closing `*/` must exist — hard-coded tails rarely
   provide one)? Does the JDBC driver tolerate the comment form you chose?
5. **Does any defense layer kill it?** Cross-check the payload against the WAF rule from
   step 1, and against driver/parser behavior.

The same method generalizes beyond SQL: for any sink, map each position your input lands in,
the constraint each position imposes, whether they conflict, and whether you can truncate the
remainder. A conflict or an un-truncatable remainder is a structural blocker.

## When to stop and declare a structural false positive

If steps 1-5 show that NO payload can survive all positions + all layers, the injection
point is **structurally unexploitable**. Call it a false positive at the GATE. Do NOT:

- keep rotating bypass syntax hoping one lands — the **structure** (not the WAF) is the wall;
- slide to "partially confirmed" because "the input does reach the sink" — reachability ≠ harm;
- insert test data into the target to manufacture a visible result — that is target tampering.

A real code weakness (non-parameterized concatenation, a bypassable WAF) may still exist and
is worth noting as **defense-in-depth** — but it is not a confirmed exploitable vulnerability,
and must not be reported as one.
