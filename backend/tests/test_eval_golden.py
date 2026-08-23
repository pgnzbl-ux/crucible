"""WP7 · 黄金集目录与分账指标（discovery-spec §10 / §12）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(EVAL))

from catalog import CatalogError, load_catalog  # noqa: E402
from metrics import (  # noqa: E402
    CaseRecord,
    CaseSnapshot,
    aggregate,
    render_markdown,
    score_case,
)
from run_golden import run  # noqa: E402


def test_catalog_has_at_least_50_cve_cases():
    cases = load_catalog()
    assert len(cases) >= 50
    assert all(c.case_id.startswith("CVE-") for c in cases)
    assert all(c.git_url.startswith("https://") for c in cases)


def test_catalog_rejects_short_dir(tmp_path: Path):
    (tmp_path / "CVE-1").mkdir()
    (tmp_path / "CVE-1" / "case.yaml").write_text(
        "id: CVE-1\ngit_url: https://example.com/a.git\nref: 1\n"
        "notes: x\nexpected:\n  - cwe: CWE-89\n    file_contains: a.py\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="不足 50"):
        load_catalog(tmp_path)


def _case(**kwargs) -> CaseRecord:
    base = dict(
        case_id="CVE-TEST",
        git_url="https://example.com/x.git",
        ref="1",
        expected=[dict(cwe="CWE-89", file_contains="app/db.py")],
        tp_samples=[dict(cwe="CWE-89", file_contains="app/db.py")],
        fp_samples=[],
    )
    base.update(kwargs)
    return CaseRecord(**base)


@pytest.mark.parametrize(
    "snap,expect",
    [
        (
            CaseSnapshot(
                raw_findings=[dict(cwe="CWE-89", file_path="app/db.py")],
                groups=[dict(
                    cwe="CWE-89", file_path="app/db.py", status="dispatched",
                    ai_verdict="tp", member_count=1,
                )],
                has_lead=True, task_verdict="confirmed",
            ),
            dict(expected_hit=1, missed=[], lead_false_positive=False, tp_samples_judged_fp=0),
        ),
        (
            CaseSnapshot(
                raw_findings=[dict(cwe="CWE-79", file_path="other.py")],
                groups=[dict(
                    cwe="CWE-79", file_path="other.py", status="needs_review",
                    ai_verdict="tp", member_count=1,
                )],
            ),
            dict(expected_hit=0, missed=["CWE-89 @ app/db.py"]),
        ),
        (
            CaseSnapshot(
                raw_findings=[dict(cwe="CWE-89", file_path="app/db.py")],
                groups=[dict(
                    cwe="CWE-89", file_path="app/db.py", status="needs_review",
                    ai_verdict="fp", member_count=1,
                )],
            ),
            dict(expected_hit=1, tp_samples_judged_fp=1, tp_samples_in_funnel=1),
        ),
        (
            CaseSnapshot(
                raw_findings=[dict(cwe="CWE-89", file_path="app/db.py")],
                groups=[dict(
                    cwe="CWE-89", file_path="app/db.py", status="dispatched",
                    ai_verdict="tp", member_count=1,
                )],
                has_lead=True, task_verdict="false_positive",
            ),
            dict(lead_false_positive=True),
        ),
    ],
)
def test_score_case_table(snap, expect):
    row = score_case(_case(), snap)
    for key, value in expect.items():
        assert getattr(row, key) == value, key


def test_aggregate_gates_and_no_unconditional_recall():
    hit = score_case(_case(), CaseSnapshot(
        raw_findings=[dict(cwe="CWE-89", file_path="app/db.py")] * 20,
        groups=[dict(
            cwe="CWE-89", file_path="app/db.py", status="needs_review",
            ai_verdict="tp", member_count=20,
        )],
        has_lead=True, task_verdict="confirmed", review_ready_seconds=100,
    ))
    report = aggregate([hit])
    md = render_markdown(report)
    assert "无线索全库挖掘召回" in md
    assert "hypothesis_coverage" in md
    assert "lead_fp_rate" in md
    assert "unconditional" not in md.lower()
    assert report.hypothesis_coverage == 1.0
    assert report.noise_compression == 20.0
    assert report.triage_precision == 1.0
    assert report.recall_redline == 0.0
    assert report.lead_fp_rate == 0.0


def test_osv_bypass_excluded_from_precision():
    row = score_case(_case(), CaseSnapshot(
        raw_findings=[dict(cwe="CWE-89", file_path="app/db.py")],
        groups=[
            dict(cwe="CWE-89", file_path="app/db.py", status="needs_review", ai_verdict="tp"),
            dict(cwe=None, file_path="pom.xml", status="adjudicated", ai_verdict="bypass"),
        ],
    ))
    report = aggregate([row])
    assert row.bypass_groups == 1
    assert report.triage_precision == 1.0


def test_run_catalog_and_mock():
    catalog_report = run("catalog", api="http://x", token=None, limit=None, poll_s=1, timeout_s=1)
    assert catalog_report.skipped_count >= 50
    mock_report = run("mock", api="http://x", token=None, limit=None, poll_s=1, timeout_s=1)
    scored = {c.case_id for c in mock_report.cases if not c.skipped}
    assert {"CVE-2022-28346", "CVE-2022-22818", "CVE-2019-19844"} <= scored
    md = render_markdown(mock_report)
    assert "分账指标" in md
    assert "recall_redline" in md
