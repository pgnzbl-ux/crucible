"""Lab 表字段、唯一约束、tasks.lab_id。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_labs_table_columns():
    from app.contexts.identity.models import User  # noqa: F401
    from app.contexts.project.models import Project  # noqa: F401
    from app.contexts.lab.models import Lab  # noqa: F401
    from app.contexts.task.models import Task  # noqa: F401
    from app.shared.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("labs")}
    expected = {
        "id", "owner_id", "project_id", "commit_sha", "status",
        "compose_project", "workdir", "target_url", "compose_path",
        "transport_shape", "initial_creds", "last_seen_at", "ttl_seconds",
        "creator_task_id", "error_message", "created_at", "updated_at",
    }
    assert expected.issubset(cols), f"缺字段: {expected - cols}"
    task_cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert "lab_id" in task_cols
    sha_col = next(c for c in inspect(engine).get_columns("labs") if c["name"] == "commit_sha")
    assert sha_col["type"].length == 64, "labs.commit_sha 须容纳 git SHA-256 / 上传包 sha256"


def test_labs_unique_owner_project_sha():
    from app.contexts.identity.models import User  # noqa: F401
    from app.contexts.project.models import Project  # noqa: F401
    from app.contexts.lab.models import Lab
    from app.contexts.task.models import Task  # noqa: F401
    from app.shared.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        kwargs = dict(
            owner_id="u1", project_id="p1", commit_sha="a" * 40,
            status="creating", compose_project="crucible-lab-x",
            workdir="/tmp/labs/x", ttl_seconds=3600,
        )
        s.add(Lab(**kwargs))
        s.commit()
        s.add(Lab(**kwargs, id="other"))
        try:
            s.commit()
            raise AssertionError("应拒绝重复 owner+project+sha")
        except IntegrityError:
            s.rollback()
