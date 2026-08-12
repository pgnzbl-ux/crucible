"""NodeRun 模型 + Task/AgentEvent 改造测试。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, create_engine


def _build_all():
    """触发所有 model 注册并建表(FK 依赖链完整)。"""
    from app.shared.base import Base
    from app.contexts.identity.models import User  # noqa: F401
    from app.contexts.project.models import Project  # noqa: F401
    from app.contexts.task.models import Task, TaskRun, AgentEvent, NodeRun  # noqa: F401
    from app.contexts.report.models import Report  # noqa: F401
    from app.contexts.settings.models import LlmProvider  # noqa: F401
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def test_all_tables_present():
    engine = _build_all()
    tables = set(inspect(engine).get_table_names())
    assert "node_runs" in tables, "缺 node_runs 表"
    assert "projects" in tables


def test_node_run_fields():
    engine = _build_all()
    cols = {c["name"] for c in inspect(engine).get_columns("node_runs")}
    expected = {
        "id", "run_id", "task_id", "node_index", "node_key", "status",
        "input_json", "output_json", "attempt", "agent_session_id",
        "error_message", "started_at", "finished_at", "created_at", "updated_at",
    }
    assert expected.issubset(cols), f"NodeRun 缺字段: {expected - cols}"


def test_task_has_project_id_and_verdict():
    engine = _build_all()
    cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert "project_id" in cols, "Task 缺 project_id"
    assert "verdict" in cols, "Task 缺 verdict"
    # 旧字段保留(兼容)
    assert "project_address" in cols, "project_address 应保留"


def test_agent_event_has_node_run_id():
    engine = _build_all()
    cols = {c["name"] for c in inspect(engine).get_columns("agent_events")}
    assert "node_run_id" in cols, "AgentEvent 缺 node_run_id"
