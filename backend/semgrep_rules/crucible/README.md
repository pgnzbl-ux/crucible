# Crucible Semgrep overlay

位于 ``backend/semgrep_rules/crucible/``，与同级社区语言目录一并扫描。
运行时由 ``SCANNER_SEMGREP_RULES_DIR``（指向 ``backend/semgrep_rules``）解析；叠加根为 ``{RULES_DIR}/crucible``。

| 语言 | 规则 ID |
|------|---------|
| `php/` | `pdo-mysqli-query-sink`, `sql-fragment-concat`, `symfony-request-sqli`, `thinkphp-raw-sql`, `codeigniter-db-query`, `php-process-cmdi`；回归夹具 `regression/zentao-chart-bi/` |
| `python/` | `fastapi-tainted-sql`, `sqlalchemy2-tainted-text`, `sqlalchemy-order-by-column`, `django-order-by-injection`, `flask-sql-fragment`, `httpx-aiohttp-ssrf` |
| `go/` | `gin-echo-sqli`, `chi-fiber-sqli`, `gorm-where-receiver`, `go-framework-ssrf`, `go-template-ssti` |
| `java/` | `mybatis-xml-dollar-interp`, `mybatis-java-select-concat`, `mybatis-java-annotation-concat`, `jdbc-sql-fragment-concat`, `jsp-scriptlet-taint`, `spring-httpclient-ssrf` |

详见上级 [`../README.md`](../README.md) 与 [`docs/semgrep-coverage-matrix.md`](../../../docs/semgrep-coverage-matrix.md)。
