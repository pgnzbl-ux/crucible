"""对象存储边界：Context 不再自建 Minio 客户端。"""
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


def test_agent_tasks_does_not_import_report_storage():
    text = (APP / "contexts" / "agent" / "tasks.py").read_text(encoding="utf-8")
    assert "from app.contexts.report import storage" not in text
    assert "app.contexts.report.storage" not in text


def test_no_minio_client_outside_object_store():
    hits: list[str] = []
    for path in APP.rglob("*.py"):
        if path.name == "object_store.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "Minio(" in text:
            hits.append(str(path.relative_to(BACKEND)))
    assert hits == []
