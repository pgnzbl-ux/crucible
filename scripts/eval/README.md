# 发现侧黄金集（WP7）

评估脚手架，口径锁在 `docs/discovery-spec.md` §2.6 / §10 / §12。**放量前必须先跑**；prompt / 评分表 / 模型 / 阈值变更后重跑对比。

## 测什么、不测什么

| 指标 | 含义 | 门禁 |
|---|---|---|
| 假设覆盖率 | `expected` 是否进入 RawFinding / AlertGroup | 只记录。引擎未打中 **不算** 二审失败 |
| 噪声压缩比 | 原始告警数 ÷ 进入人工视野（`needs_review` + `dispatched` + `resolved`）的组数 | ≥ 10:1 |
| 二审精确率 | `ai_verdict=tp` 的组中命中 `expected` 的比例（不含 osv `bypass`） | ≥ 80% |
| 召回红线 | 已进漏斗的 `tp_samples` 被判 `fp` 的比例 | ≤ 5% |
| 线索条件成功率 | 有主线索且终认完成后 `task.verdict=false_positive` 的比例 | ≤ 50%；超线先改描述模板 / grade 闸，不改模型 |
| 端到端时效 | 扫描+聚类完成，且 high priority 组已二审（复核台可开工） | 10 万行试点 ≤ 30 分钟 |

**禁止**把「不给线索让 AI 自己找洞」的召回当作门禁或调参目标。

## 用例格式

`golden/<CVE>/case.yaml`：

```yaml
id: CVE-2022-28346
language: python
git_url: https://github.com/django/django.git
ref: "4.0.3"          # 漏洞版（修复前最后一个 tag / commit）
notes: |
  说明：为什么算这条、修复版本。
expected:
  - cwe: CWE-89
    file_contains: django/db/models/query.py
    description: QuerySet.alias SQL 注入
labels:
  tp_samples:          # 缺省=expected；用于召回红线
    - cwe: CWE-89
      file_contains: django/db/models/query.py
  fp_samples: []       # 已知噪声，不进召回红线分母
```

来源：已知 CVE 的修复 commit 对，优先 Java / Python Web。`file_contains` 是路径子串，不是精确行号。

## 怎么跑

仓库根、用项目 `.venv`：

```bash
# 1) 只校验目录与 schema（≥50、字段齐全）。不调 API、不跑引擎。
./.venv/bin/python scripts/eval/run_golden.py --mode catalog

# 2) 用 fixtures/*.json 合成报告（CI / 无扫描器时）。无 fixture 的用例记为 skipped。
./.venv/bin/python scripts/eval/run_golden.py --mode mock

# 3) 对真实 API 创建 task_type=discovery（需 API + worker + 扫描器）。
export CRUCIBLE_TOKEN=...
./.venv/bin/python scripts/eval/run_golden.py --mode live --api http://127.0.0.1:8010
```

报告写到 `scripts/eval/out/latest.md`（git 忽略）。调试可加 `--limit 3`。

Live 前置：Celery worker（启动时会按锁定版本把 gitleaks/osv-scanner 装进当前 `.venv/bin`）、`llm_gateway_enabled` 按你要测的是 mock 二审还是真模型来设。时效口径是复核台就绪，不是全部组 `adjudicated`。

## 阈值怎么校准

1. 先看假设覆盖率缺口列表——未打中就补规则包 / 确认 `ref` 是否真是漏洞版，**不要**因此改 triage prompt。
2. 噪声压缩比不够：查聚类键和攻击面降权，不是提高 FP 阈值。
3. 召回红线破线：降 `triage` 把 TP 判成 FP 的攻击性（评分表 / 路由阈值），不要藏未审为 FP。
4. 线索条件成功率 >50%：改 dispatch 描述模板或 A 级闸（无 `source_to_sink` 不得自动终认）。
5. 二审精确率：只在「漏斗内的假设」上调模型 / 评分表。

`fixtures/` 里几份 JSON 只服务 mock 模式与单测，不是生产基线。

增补用例：在 `scripts/eval/emit_golden.py` 的 `CASES` 追加一条后执行：

```bash
./.venv/bin/python scripts/eval/emit_golden.py
```
