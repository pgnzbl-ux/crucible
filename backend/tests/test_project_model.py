"""Project 模型字段与建表测试。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, create_engine


def test_project_table_columns():
    from app.contexts.project.models import Project  # noqa: F401
    from app.contexts.identity.models import User  # noqa: F401 — 注册 users 表给 FK
    from app.shared.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("projects")}
    expected = {
        "id", "name", "git_url", "default_ref", "default_ref_type", "description", "owner_id",
        "detected_language", "detected_framework", "is_web",
        "last_cloned_at", "source_type", "created_at", "updated_at",
    }
    assert expected.issubset(cols), f"缺字段: {expected - cols}"


def test_project_required_fields():
    from app.contexts.project.models import Project

    p = Project(name="x", git_url="https://github.com/a/b.git", owner_id="u1")
    assert p.name == "x"
    assert p.git_url == "https://github.com/a/b.git"
    assert p.is_web is None


def test_project_owner_name_unique():
    from sqlalchemy import inspect as sa_inspect

    from app.contexts.identity.models import User  # noqa: F401
    from app.contexts.project.models import Project  # noqa: F401
    from app.shared.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    uniques = {tuple(u["column_names"]) for u in sa_inspect(engine).get_unique_constraints("projects")}
    assert ("owner_id", "name") in uniques
