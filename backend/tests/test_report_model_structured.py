"""Report 结构化字段测试。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, create_engine


def test_report_structured_fields():
    from app.shared.base import Base
    from app.contexts.report.models import Report  # noqa: F401
    from app.contexts.identity.models import User  # noqa: F401
    from app.contexts.project.models import Project  # noqa: F401
    from app.contexts.task.models import Task  # noqa: F401 — FK 链

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("reports")}
    expected = {
        "verdict", "cvss_score", "severity", "vulnerable_file",
        "report_data", "md_artifact_key", "docx_artifact_key",
    }
    assert expected.issubset(cols), f"Report 缺字段: {expected - cols}"
    # 旧字段保留(兼容期)
    assert "reasoning" in cols, "reasoning 应保留(deprecated)"
