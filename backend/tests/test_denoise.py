"""确定性降噪表驱动测试(discovery-spec §2.5 C 档)。"""
from __future__ import annotations

import pytest

from app.contexts.finding.denoise import is_c_grade, partition_for_cluster
from app.contexts.finding.clustering import grade_for


def _f(**kwargs):
    base = {
        "engine": "semgrep",
        "rule_id": "python.sqli",
        "cwe": "CWE-89",
        "severity": "error",
        "file_path": "app/db.py",
        "line_start": 10,
        "message": "sqli",
        "source_to_sink": None,
        "code_snippet": None,
        "raw": {},
    }
    base.update(kwargs)
    if "raw" in kwargs and kwargs["raw"] is not None:
        base["raw"] = kwargs["raw"]
    return base


@pytest.mark.parametrize(
    "finding,expect_c",
    [
        (_f(raw={"confidence": "LOW", "has_dataflow": False}), True),
        (_f(raw={"confidence": "LOW", "has_dataflow": True}, source_to_sink=["a:1"]), False),
        (_f(raw={"confidence": "UNKNOWN"}), False),
        (_f(raw={"confidence": "HIGH", "category": "security"}), False),
        (_f(raw={"category": "best-practice", "confidence": "MEDIUM"}), True),
        (_f(engine="osv", cwe=None, raw={"called": False}), False),
        (_f(
            engine="gitleaks", cwe="CWE-798",
            raw={"rule_class": "generic"},
            message="API_KEY=YOUR_SECRET_HERE",
        ), True),
        (_f(
            engine="gitleaks", cwe="CWE-798", file_path="docs/setup.md",
            raw={"rule_class": "generic"}, message="key=abc1234567890",
        ), True),
        (_f(
            engine="gitleaks", cwe="CWE-798", file_path="config/prod.env",
            raw={"rule_class": "known"}, message="AKIA…MPLE***[len=20]",
        ), False),
        (_f(
            engine="gitleaks", cwe="CWE-798", file_path="app/config.py",
            raw={"rule_class": "generic"}, message="token=live_abc_not_placeholder",
        ), False),
        # 缺字段保守保留
        (_f(raw={}), False),
    ],
)
def test_is_c_grade_table(finding, expect_c):
    assert is_c_grade(finding) is expect_c


def test_partition_counts_by_engine():
    keep, dropped, by_eng = partition_for_cluster([
        _f(id="1", raw={"confidence": "LOW"}),
        _f(id="2", raw={"confidence": "HIGH", "has_dataflow": True}, source_to_sink=["x:1"]),
        _f(
            id="3", engine="gitleaks", cwe="CWE-798",
            raw={"rule_class": "generic"}, message="changeme",
        ),
    ])
    assert len(keep) == 1 and keep[0]["id"] == "2"
    assert len(dropped) == 2
    assert by_eng == {"semgrep": 1, "gitleaks": 1}


@pytest.mark.parametrize(
    "finding,expect_grade",
    [
        (_f(source_to_sink=["a:1"], raw={"confidence": "UNKNOWN", "has_dataflow": True}), "A"),
        (_f(source_to_sink=["a:1"], raw={"confidence": "HIGH", "has_dataflow": True}), "A"),
        (_f(source_to_sink=["a:1"], raw={"confidence": "MEDIUM", "has_dataflow": True}), "B"),
        (_f(raw={}), "B"),  # locus+cwe, no flow
        (_f(
            engine="gitleaks", cwe="CWE-798",
            raw={"rule_class": "known"},
        ), "A"),
        (_f(
            engine="gitleaks", cwe="CWE-798",
            raw={"rule_class": "generic"},
        ), "B"),
        (_f(engine="osv", cwe=None, line_start=None), None),
        (_f(cwe=None, line_start=None, file_path="", rule_id="x"), "F"),
    ],
)
def test_grade_for_enriched(finding, expect_grade):
    assert grade_for(finding) == expect_grade


def test_build_pack_includes_evidence_metadata():
    from types import SimpleNamespace

    from app.contexts.finding.hypothesis import build_pack

    group = SimpleNamespace(
        cwe="CWE-89", clue_grade="A", file_path="app/db.py",
        function_symbol="handler", line_span="10-12",
    )
    rep = SimpleNamespace(
        source_to_sink=["a.py:1", "app/db.py:10"],
        raw={"has_dataflow": True, "confidence": "HIGH", "rule_class": None},
    )
    pack = build_pack(group=group, representative=rep, slices=[])
    assert pack is not None
    assert pack.has_dataflow is True
    assert pack.rule_class is None
    assert pack.grade == "A"

    group_g = SimpleNamespace(
        cwe="CWE-798", clue_grade="A", file_path="config.env",
        function_symbol=None, line_span="1-1",
    )
    rep_g = SimpleNamespace(
        source_to_sink=[],
        raw={"rule_class": "known"},
    )
    pack_g = build_pack(group=group_g, representative=rep_g, slices=[])
    assert pack_g is not None
    assert pack_g.rule_class == "known"
    assert pack_g.has_dataflow is False


def test_build_pack_rejects_non_ab_grade():
    from types import SimpleNamespace

    from app.contexts.finding.hypothesis import build_pack

    group = SimpleNamespace(
        cwe="CWE-89", clue_grade="F", file_path="app/db.py",
        function_symbol="h", line_span="1-1",
    )
    rep = SimpleNamespace(source_to_sink=[], raw={})
    assert build_pack(group=group, representative=rep, slices=[]) is None
