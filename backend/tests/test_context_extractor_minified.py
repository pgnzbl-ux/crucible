"""压缩产物切片保护：context_extractor 对单行超长文件返回有界占位。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.finding.context_extractor import context_around, read_function_source


def _write_minified(root: Path, name: str = "zui.zentao.js") -> Path:
    p = root / name
    p.write_bytes(b"var x=1;" * 60_000)  # 420KB 单行
    return p


def test_read_function_source_placeholder_for_minified(tmp_path):
    _write_minified(tmp_path)
    entry = {"file": "zui.zentao.js", "symbol": "f", "start_line": 1, "end_line": 2}
    out = read_function_source(str(tmp_path), entry)
    assert out is not None
    assert "压缩" in out
    assert "read_slice" in out
    assert len(out) < 300  # 有界，不产出超预算巨型切片


def test_context_around_placeholder_for_minified(tmp_path):
    _write_minified(tmp_path)
    out = context_around(str(tmp_path), "zui.zentao.js", 1)
    assert out is not None
    assert "压缩" in out
    assert len(out) < 300


def test_normal_files_keep_numbered_slices(tmp_path):
    p = tmp_path / "app.js"
    p.write_text("function f() {\n  return location.hash;\n}\n", encoding="utf-8")
    entry = {"file": "app.js", "symbol": "f", "start_line": 1, "end_line": 3}
    out = read_function_source(str(tmp_path), entry)
    assert "location.hash" in out
    assert out.startswith("1\t")

    around = context_around(str(tmp_path), "app.js", 2)
    assert "2\t" in around
