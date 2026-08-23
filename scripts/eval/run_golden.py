#!/usr/bin/env python3
"""黄金集 runner：catalog / mock / live。

示例：
  /home/ubuntu/Crucible/.venv/bin/python scripts/eval/run_golden.py --mode catalog
  /home/ubuntu/Crucible/.venv/bin/python scripts/eval/run_golden.py --mode mock
  /home/ubuntu/Crucible/.venv/bin/python scripts/eval/run_golden.py --mode live \\
      --api http://127.0.0.1:8010 --token "$TOKEN"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

from catalog import GOLDEN_DIR, load_catalog  # noqa: E402
from metrics import (  # noqa: E402
    AggregateReport,
    CaseRecord,
    CaseSnapshot,
    aggregate,
    render_markdown,
    score_case,
)

FIXTURE_DIR = EVAL_DIR / "fixtures"
OUT_DIR = EVAL_DIR / "out"
TERMINAL = frozenset({"completed", "failed", "cancelled", "needs_review"})


def _load_fixture(case_id: str) -> CaseSnapshot:
    path = FIXTURE_DIR / f"{case_id}.json"
    if not path.is_file():
        return CaseSnapshot(skipped="无 mock fixture，未跑真实任务")
    data = json.loads(path.read_text(encoding="utf-8"))
    return CaseSnapshot(
        raw_findings=list(data.get("raw_findings") or []),
        groups=list(data.get("groups") or []),
        has_lead=bool(data.get("has_lead")),
        task_verdict=data.get("task_verdict"),
        review_ready_seconds=data.get("review_ready_seconds"),
    )


def _http_json(method: str, url: str, token: str | None, body: dict | None = None, timeout: int = 60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _live_snapshot(case: CaseRecord, api: str, token: str, poll_s: int, timeout_s: int) -> CaseSnapshot:
    ref_type = "tag" if case.ref[:1].isdigit() or case.ref.startswith("v") else "commit"
    created = _http_json("POST", f"{api}/api/v1/tasks/", token, {
        "project_address": case.git_url,
        "project_ref": case.ref,
        "project_ref_type": ref_type if ref_type == "tag" or len(case.ref) >= 7 else None,
        "clone_depth": 0,
        "task_type": "discovery",
        "priority": "medium",
    })
    task_id = created["id"]
    t0 = time.time()
    status = created.get("status") or "pending"
    while time.time() - t0 < timeout_s:
        detail = _http_json("GET", f"{api}/api/v1/tasks/{task_id}", token)
        status = detail.get("status") or status
        if status in TERMINAL:
            break
        time.sleep(poll_s)
    else:
        return CaseSnapshot(skipped=f"live 超时 status={status}")

    if status == "failed":
        return CaseSnapshot(skipped=f"任务失败 {task_id}")

    groups: list[dict] = []
    offset = 0
    while True:
        page = _http_json(
            "GET",
            f"{api}/api/v1/findings/groups?task_id={task_id}&limit=200&offset={offset}",
            token,
        )
        items = page.get("items") or []
        groups.extend(items)
        if offset + len(items) >= int(page.get("total") or 0) or not items:
            break
        offset += len(items)

    raw: list[dict] = []
    for g in groups:
        gid = g.get("id")
        if not gid:
            continue
        detail = _http_json("GET", f"{api}/api/v1/findings/groups/{gid}", token)
        members = detail.get("members") or []
        for m in members:
            raw.append({
                "cwe": m.get("cwe"),
                "file_path": m.get("file_path"),
                "engine": m.get("engine"),
            })
        if not members:
            raw.append({"cwe": g.get("cwe"), "file_path": g.get("file_path"), "engine": None})

    task = _http_json("GET", f"{api}/api/v1/tasks/{task_id}", token)
    has_lead = bool(task.get("source_alert_group_id"))
    return CaseSnapshot(
        raw_findings=raw,
        groups=groups,
        has_lead=has_lead,
        task_verdict=task.get("verdict"),
        review_ready_seconds=time.time() - t0,
    )


def run(mode: str, *, api: str, token: str | None, limit: int | None,
        poll_s: int, timeout_s: int) -> AggregateReport:
    cases = load_catalog()
    if limit:
        cases = cases[:limit]
    rows = []
    for case in cases:
        if mode == "catalog":
            snap = CaseSnapshot(skipped="catalog 模式只校验用例，不跑任务")
        elif mode == "mock":
            snap = _load_fixture(case.case_id)
        else:
            if not token:
                raise SystemExit("live 模式需要 --token 或 CRUCIBLE_TOKEN")
            try:
                snap = _live_snapshot(case, api.rstrip("/"), token, poll_s, timeout_s)
            except urllib.error.URLError as exc:
                snap = CaseSnapshot(skipped=f"API 不可达: {exc}")
        rows.append(score_case(case, snap))
    return aggregate(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Crucible 发现侧黄金集评估")
    parser.add_argument("--mode", choices=("catalog", "mock", "live"), default="catalog")
    parser.add_argument("--api", default=os.environ.get("CRUCIBLE_API", "http://127.0.0.1:8010"))
    parser.add_argument("--token", default=os.environ.get("CRUCIBLE_TOKEN"))
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个用例（调试）")
    parser.add_argument("--poll", type=int, default=15)
    parser.add_argument("--timeout", type=int, default=2400, help="单用例最长等待秒")
    parser.add_argument("--out", default=str(OUT_DIR / "latest.md"))
    args = parser.parse_args()

    report = run(
        args.mode, api=args.api, token=args.token,
        limit=args.limit or None, poll_s=args.poll, timeout_s=args.timeout,
    )
    md = render_markdown(report)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    sys.stdout.write(f"\n写入 {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
