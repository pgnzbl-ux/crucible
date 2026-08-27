# Semgrep 覆盖矩阵（社区树 × Crucible overlay）

> 规则包根：`backend/semgrep_rules/`（``.env`` → `SCANNER_SEMGREP_RULES_DIR`）。
> 社区语言目录与 `crucible/` 叠加同根；扫描入口 `scan_semgrep` + `semgrep_rules.py`，
> `oss_only`，不接 Registry。

**原则：** Semgrep 负责单点/短链高召回；跨模块业务链仍靠 finding 触发后的 audit Agent。

**评分口径：** 相对本仓库「优先语言主流触发面可答清」的平台自评（非商业 SAST 对标）。目标总分约 **9**；跨模块多跳仍约 **6**。

图例：`社区` = upstream 规则树；`叠加` = Crucible overlay；`缺口` = 已知弱/无覆盖。

---

## 0. 分项评分（验收 scorecard）

| 维度 | 现约 | 说明 |
|------|------|------|
| PHP 自研/主流框架注入 | **9+** | PDO/mysqli + 片段；Symfony / ThinkPHP / CodeIgniter；Process CMDi |
| Python 框架注入 + SSRF | **9** | FastAPI/Flask/SQLAlchemy 2.0 / Django `order_by`；httpx·aiohttp SSRF |
| Go Web 框架注入 + SSRF | **9** | Gin/Echo + chi/fiber；GORM Where（含字段 receiver）；框架 SSRF；template SSTI |
| Java·JSP / MyBatis | **8.5–9** | MyBatis `${}` / Java select 拼接；JDBC 片段；JSP scriptlet；Spring HTTP SSRF |
| 跨模块多跳 | **~6** | 片段规则出线索；**不承诺**真 interfile taint |
| 噪声可控 | **≥7** | 夹具回归 + medium/high confidence；禁列名全家桶 / 裸 `$C.get` |
| **总分（优先四语言）** | **~9** | JS/TS Nest 等本期不抬分 |

---

## 1. 语言 × 框架 × CWE-89（SQL 注入）/ OWASP A03

| 语言 | 框架 / 形态 | 社区 | 叠加 | 缺口 |
|------|-------------|------|------|------|
| PHP | 裸 PHP / 自研 MVC（禅道） | `tainted-sql-string`（字面量须含 SELECT…）；**无 PDO/mysqli sink** | `pdo-mysqli-query-sink`、`sql-fragment-concat` | 跨 control→model 多跳仍可能断 |
| PHP | Laravel | whereRaw / DB::raw 等（中等） | `php-process-cmdi`（Illuminate Process） | — |
| PHP | Doctrine | QueryBuilder / DBAL（中等） | — | — |
| PHP | Symfony | **无 SQLi 源** | `symfony-request-sqli` | 深封装 Repository 跨文件弱 |
| PHP | ThinkPHP / CodeIgniter | **无** | `thinkphp-raw-sql`、`codeigniter-db-query` | 非特色 API 的自封装 query |
| PHP | WordPress | `$wpdb->query` audit | — | 仅插件路径 |
| Python | Django | raw/extra/RawSQL/cursor 较全 | `django-order-by-injection` | 跨 app 层弱 |
| Python | Flask | request→SQL 关键字串 | `flask-sql-fragment` | 跨 service 层易漏 |
| Python | FastAPI | **仅 CORS** | `fastapi-tainted-sql` | 跨层弱 |
| Python | SQLAlchemy | format/execute/text 少量 | `sqlalchemy2-tainted-text`、`sqlalchemy-order-by-column` | 动态列名全家桶（故意不做） |
| Go | net/http | taint SQLi（偏关键字） | — | — |
| Go | database/sql | 句法拼接 | Gin/Echo/chi/fiber 源可达 | 非框架命名封装 |
| Go | GORM | Raw/Order/Exec…；**无 Where** | `gin-echo-sqli`、`chi-fiber-sqli`、`gorm-where-receiver` | 极端别名 receiver |
| Go | gin / echo / chi / fiber | **无包** | `gin-echo-sqli`、`chi-fiber-sqli` | `ShouldBind*` 侧效应污点未覆盖 |
| Java | JDBC / Spring JDBC | 短链较全 | `jdbc-sql-fragment-concat` | 无 SELECT 锚点的片段优先 |
| Java | MyBatis | 部分 `${}` / annotation | `mybatis-xml-dollar-interp`、`mybatis-java-select-concat`、`mybatis-java-annotation-concat` | 动态 SQL provider 复杂拼装 |
| Java | JSP | 薄 / 偏 audit | `jsp-scriptlet-taint` | EL 间接链 |
| Java | Spring MVC | 短链 SQLi | —（社区已有） | 自封装 DAO |
| JS/TS | Express | 最厚 | — | 自封装 Repository 跨文件弱 |
| JS/TS | NestJS | 偏 audit | — | 注入面薄（本期不抬） |

---

## 2. CWE Top 25 / OWASP Top 10 摘要（平台视角）

| 主题 | CWE / OWASP | 社区 | 叠加 | Crucible 风险 |
|------|-------------|------|------|---------------|
| SQL 注入 | CWE-89 / A03 | 有；PHP sink/关键字缺口；FastAPI/Gin/Java MyBatis 弱 | PHP/Python/Go/Java overlay 全波 | **高**（禅道已实锤；片段先出线索） |
| 命令注入 | CWE-78 | php/python/go 有 | `php-process-cmdi` | 中；封装 executor 仍漏 |
| XSS | CWE-79 / A03 | JS/PHP/Django/Flask 密 | JSP scriptlet 部分 | 中；噪声大 |
| 反序列化 | CWE-502 | pickle/unserialize/yaml | — | 中 |
| 路径穿越 | CWE-22 | open/join 类 | — | 中 |
| SSRF | CWE-918 | requests/urllib/http.Get | `httpx-aiohttp-ssrf`、`go-framework-ssrf`、`spring-httpclient-ssrf` | 中高；allowlist sanitizer |
| SSTI | — | Flask/Jinja/Go template | `go-template-ssti` | 中 |
| XXE | CWE-611 | Python defused；Go 弱 | — | 中 |
| 鉴权/CSRF/JWT | A01/A07 | Django/Flask/JWT；FastAPI/Gin 弱 | — | 中 |
| 供应链 | A06 | 非 semgrep（OSV） | — | 已分流 |
| 硬编码密钥 | CWE-798 | gitleaks + 弱规则 | — | 已分流 |

`problem-based-packs` 几乎只有 insecure-transport，**不能**当 OWASP Top10 规则包。

---

## 3. Overlay 规则清单（全量）

### PHP

| ID | 模式 | CWE | 说明 |
|----|------|-----|------|
| `pdo-mysqli-query-sink` | taint | CWE-89 | `$_GET/POST/…` → PDO/mysqli/`queryWithDriver` |
| `sql-fragment-concat` | 句法 | CWE-89 | IN/implode、`'".$x."'`、`` `$field` ``、`where`/`ORDER BY`/`LIMIT`/`LIKE`/` AND `/` OR ` 等无 SELECT 锚点 |
| `symfony-request-sqli` | taint | CWE-89 | Symfony `Request` → DBAL / EM / QB |
| `thinkphp-raw-sql` | taint | CWE-89 | 超全局/`param`/`input` → ThinkPHP `Db::query|raw` / `whereRaw` |
| `codeigniter-db-query` | taint | CWE-89 | CI 输入 → `db->query` / `order_by` |
| `php-process-cmdi` | taint | CWE-78 | 超全局/Request → Symfony/Illuminate `Process` |

### Python

| ID | 模式 | CWE | 说明 |
|----|------|-----|------|
| `fastapi-tainted-sql` | taint | CWE-89 | FastAPI Query/Path/Body/Request → execute/SQL 串 |
| `sqlalchemy2-tainted-text` | taint | CWE-89 | Flask/Django/FastAPI → `text()` / `execute(text)` / `exec_driver_sql` |
| `sqlalchemy-order-by-column` | taint | CWE-89 | 同上源 → `order_by` / `order_by(text(...))`（whitelist sanitizer） |
| `django-order-by-injection` | taint | CWE-89 | `request.GET/POST` → `QuerySet.order_by` / `extra(order_by=…)` |
| `flask-sql-fragment` | 句法/薄 taint | CWE-89 | Flask request → where/order/limit 片段或 `cursor.execute` |
| `httpx-aiohttp-ssrf` | taint | CWE-918 | 同上源 → httpx / aiohttp（须 `import httpx|aiohttp`） |

### Go

| ID | 模式 | CWE | 说明 |
|----|------|-----|------|
| `gin-echo-sqli` | taint | CWE-89 | Gin/Echo Query/Param/PostForm → database/sql 与 GORM（含 Where） |
| `chi-fiber-sqli` | taint | CWE-89 | chi / fiber 源 → sql + GORM |
| `gorm-where-receiver` | taint | CWE-89 | 框架/`http.Request` → `$R.$DB.Where|Raw`（字段 receiver） |
| `go-framework-ssrf` | taint | CWE-918 | gin/echo/chi/fiber → `http.Get` / `NewRequest*` / `Client.Do` |
| `go-template-ssti` | taint | SSTI | 同上源 → `text/html/template` `.Parse($T)` |

### Java / JSP

| ID | 模式 | CWE | 说明 |
|----|------|-----|------|
| `mybatis-xml-dollar-interp` | generic | CWE-89 | Mapper XML 中 `${…}`（排除 `#{}`） |
| `mybatis-java-select-concat` | taint | CWE-89 | `@Request*` / `getParameter` → SqlSession select/update… |
| `mybatis-java-annotation-concat` | 句法 | CWE-89 | `@Select`/`@Update`/… 注解 SQL 字符串拼接 |
| `jdbc-sql-fragment-concat` | 句法 | CWE-89 | `" WHERE "|ORDER BY|AND ` + 变量 |
| `jsp-scriptlet-taint` | regex `*.jsp` | CWE-89/79/78 | scriptlet 内 `getParameter` → execute / `out.print` / `Runtime.exec` |
| `spring-httpclient-ssrf` | taint | CWE-918 | Spring 注解源 → RestTemplate / WebClient / OkHttp / HttpClient |

---

## 4. 禅道回归证据（蒸馏夹具）

完整工作区 `/tmp/crucible/audit-…/zentaopms` 可能不在；夹具蒸馏自已知链路：

`POST filters` → `json_decode` → `getFilterFormat` implode → `getMultiData` WHERE 片段 → `queryWithDriver` / `PDO::query`。

路径：`backend/semgrep_rules/crucible/php/regression/zentao-chart-bi/`。

```bash
mkdir -p .semgrep-xdg/.semgrep
export XDG_CONFIG_HOME=/home/ubuntu/Crucible/.semgrep-xdg
export SEMGREP_LOG_FILE=$XDG_CONFIG_HOME/.semgrep/semgrep.log
export SEMGREP_SETTINGS_FILE=$XDG_CONFIG_HOME/.semgrep/settings.yml
export SEMGREP_VERSION_CACHE_PATH=$XDG_CONFIG_HOME/.semgrep/semgrep_version

# 仅 overlay（夹具回归）
.venv/bin/semgrep --disable-version-check scan --oss-only --metrics=off \
  --config backend/semgrep_rules/crucible/php \
  backend/semgrep_rules/crucible/php/regression/zentao-chart-bi

# 社区 php + overlay（与 scan_semgrep / .env 一致）
.venv/bin/semgrep --disable-version-check scan --oss-only --metrics=off \
  --config backend/semgrep_rules/php \
  --config backend/semgrep_rules/crucible/php \
  backend/semgrep_rules/crucible/php/regression/zentao-chart-bi
```

**实测（蒸馏夹具，仅 overlay）：** `sql-fragment-concat` 命中 chart/bi model 片段路径；≥1 条 CWE-89。跨 screen→chart→bi 多跳仍靠片段规则先出线索。

自动化：`backend/tests/test_semgrep_overlay_regression.py`（禅道 + python/go/java 关键夹具 JSON scan）。

规则自测：

```bash
.venv/bin/semgrep --disable-version-check --test backend/semgrep_rules/crucible/php/security
.venv/bin/semgrep --disable-version-check --test backend/semgrep_rules/crucible/python
.venv/bin/semgrep --disable-version-check --test backend/semgrep_rules/crucible/go
.venv/bin/semgrep --disable-version-check --test backend/semgrep_rules/crucible/java
```

---

## 5. 接线

``SCANNER_SEMGREP_RULES_DIR`` → ``backend/semgrep_rules``（见 ``backend/.env``）。

`SemgrepNode._config_paths`：对每个 profile 映射出的语言：

1. `{RULES_DIR}/<lang>/`（社区）
2. `{RULES_DIR}/crucible/<lang>/`（叠加，含 **java**）

`config_summary` 含 `overlay_configs`。

---

## 6. 明确不做 / 剩余缺口

- 不改写 upstream `semgrep-rules` 大树；不接付费 Registry
- 不做：列名注入全家桶、裸 `$C.get($URL)`、通用 `$OBJ->query` 启发式、重复社区已有短链
- **多跳仍 ~6**：无 finding 时不指望二审全库挖 SQLi；跨文件业务链靠 audit
- JS/TS Nest、复杂 MyBatis Provider、`ShouldBind*` 侧效应污点：后续可选
