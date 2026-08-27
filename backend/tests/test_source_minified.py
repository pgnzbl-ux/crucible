"""压缩产物检测：is_minified 判定 + scan 统计 + source 节点挂钩。"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.source import SourceNode
from app.contexts.project.source_acquire import SourceAcquireResult
from app.contexts.project.source_minified import (
    MINIFIED_MIN_BYTES,
    is_minified,
    scan_minified_files,
)


def _write_sized(path: Path, size: int, lines: int) -> None:
    """写一个指定总字节数与行数的文本文件（每行等长）。"""
    if lines <= 1:
        path.write_bytes(b"a" * size)
        return
    per = size // lines
    body = (b"a" * (per - 1) + b"\n") * (lines - 1)
    body += b"a" * (size - len(body))
    path.write_bytes(body)


def _ctx(tmp_path, on_event=None) -> NodeContext:
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="https://github.com/siteboon/claudecodeui.git",
        project_ref="main",
    )
    ctx.on_event = on_event
    return ctx


def _acquire_ok(dest: Path) -> SourceAcquireResult:
    return SourceAcquireResult(
        ok=True,
        origin="git",
        git_url_original="https://github.com/siteboon/claudecodeui.git",
        git_url_normalized="https://github.com/siteboon/claudecodeui",
        project_key="siteboon/claudecodeui",
        git_host="github.com",
        repo_dirname="claudecodeui",
        ref_type="branch",
        ref_name="main",
        commit_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        project_path=str(dest),
        top_level=["README.md"],
        file_count=2,
    )


# ── 判定 ──


def test_is_minified_hits_big_few_lines(tmp_path):
    p = tmp_path / "zui.zentao.js"
    _write_sized(p, 400_000, 3)
    assert is_minified(p) is True


def test_is_minified_allows_big_many_lines(tmp_path):
    p = tmp_path / "jquery.js"
    _write_sized(p, 400_000, 3000)
    assert is_minified(p) is False


def test_is_minified_allows_small_one_liner(tmp_path):
    p = tmp_path / "tiny.js"
    p.write_bytes(b"var a=1;" * 1000)  # 8KB 单行，未达大小门槛
    assert is_minified(p) is False


def test_is_minified_missing_file(tmp_path):
    assert is_minified(tmp_path / "nope.js") is False


# ── 扫描 ──


def test_scan_minified_files_prunes_and_ranks(tmp_path):
    js_dir = tmp_path / "www" / "js"
    js_dir.mkdir(parents=True)
    _write_sized(js_dir / "a.js", 500_000, 2)
    _write_sized(js_dir / "b.css", 400_000, 1)
    _write_sized(js_dir / "normal.js", MINIFIED_MIN_BYTES, 3000)
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    _write_sized(nm / "c.js", 600_000, 2)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    _write_sized(git_dir / "d.pack", 700_000, 1)

    stats = scan_minified_files(tmp_path)
    assert stats["count"] == 2
    assert stats["total_bytes"] == 900_000
    assert stats["top"][0] == "www/js/a.js"
    assert "www/js/b.css" in stats["top"]


# ── source 节点挂钩 ──


async def test_source_node_reports_minified_stats(tmp_path):
    dest = tmp_path / "claudecodeui"
    dest.mkdir()
    (dest / "README.md").write_text("# demo\n")
    _write_sized(dest / "zui.zentao.js", 400_000, 3)
    events: list[dict] = []

    with patch(
        "app.contexts.project.source_acquire.acquire_source",
        return_value=_acquire_ok(dest),
    ):
        out = await SourceNode().execute(_ctx(tmp_path, on_event=events.append))

    assert out["minified_stats"]["count"] == 1
    assert out["minified_stats"]["top"] == ["zui.zentao.js"]
    assert any("压缩产物" in e["message"] for e in events)


async def test_source_node_scan_failure_is_soft(tmp_path):
    dest = tmp_path / "claudecodeui"
    dest.mkdir()
    (dest / "README.md").write_text("# demo\n")

    with (
        patch(
            "app.contexts.project.source_acquire.acquire_source",
            return_value=_acquire_ok(dest),
        ),
        patch(
            "app.contexts.project.source_minified.scan_minified_files",
            side_effect=OSError("boom"),
        ),
    ):
        out = await SourceNode().execute(_ctx(tmp_path))

    assert out["repo_dirname"] == "claudecodeui"  # 节点未因盘点失败而失败
    assert out["minified_stats"]["count"] == 0
    assert "boom" in out["minified_stats"]["error"]
