"""runner 压缩产物治理：Read/Grep 拦截钩子 + read_slice 有界读取。"""
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "backend",
        "agent-runner",
    ),
)

# 容器入口依赖 SDK；单测只验证拦截与切片逻辑，不真调 query()
sys.modules.setdefault("claude_agent_sdk", MagicMock())


@pytest.fixture(autouse=True)
def _sdk_module_stub():
    """每个测试独立的 claude_agent_sdk stub，测试后还原，杜绝跨文件全局污染。"""
    prev = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = MagicMock()
    yield
    if prev is None:
        sys.modules.pop("claude_agent_sdk", None)
    else:
        sys.modules["claude_agent_sdk"] = prev


from runner.policies import is_minified_file, make_read_guard_hook  # noqa: E402
from runner.tools import (  # noqa: E402
    READ_SLICE_MAX_MATCHES,
    READ_SLICE_MAX_OUTPUT,
    make_read_slice_tool,
    read_slice_impl,
)


def _write_sized(path: Path, size: int, lines: int) -> None:
    if lines <= 1:
        path.write_bytes(b"a" * size)
        return
    per = size // lines
    body = (b"a" * (per - 1) + b"\n") * (lines - 1)
    body += b"a" * (size - len(body))
    path.write_bytes(body)


def _minified_file(tmp_path: Path, name: str = "bundle.min.js") -> Path:
    p = tmp_path / name
    _write_sized(p, 400_000, 3)
    return p


# ── is_minified_file ──


def test_is_minified_file_classifies_by_size_and_lines(tmp_path):
    hit, size, lines = is_minified_file(_minified_file(tmp_path))
    assert (hit, size, lines) == (True, 400_000, 3)

    normal = tmp_path / "normal.js"
    _write_sized(normal, 400_000, 3000)
    hit, _, _ = is_minified_file(normal)
    assert hit is False

    assert is_minified_file(tmp_path / "nope.js")[0] is False


# ── read guard hook ──


async def test_read_guard_denies_read_on_minified(tmp_path):
    p = _minified_file(tmp_path)
    events: list[dict] = []
    hook = make_read_guard_hook(events.append, workspace_root=str(tmp_path))
    res = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": str(p), "offset": 280, "limit": 15}},
        None,
        None,
    )
    decision = res["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    reason = decision["permissionDecisionReason"]
    assert "read_slice" in reason
    assert "不要重试 Read" in reason
    assert len(events) == 1
    assert events[0]["type"] == "tool.call.denied"
    assert events[0]["tool"] == "Read"
    assert "minified artifact" in events[0]["reason"]


async def test_read_guard_denies_grep_on_minified(tmp_path):
    p = _minified_file(tmp_path)
    hook = make_read_guard_hook(lambda e: None, workspace_root=str(tmp_path))
    res = await hook(
        {"tool_name": "Grep", "tool_input": {"pattern": "eval", "path": str(p)}},
        None,
        None,
    )
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_read_guard_allows_normal_and_missing_targets(tmp_path):
    normal = tmp_path / "normal.js"
    _write_sized(normal, 400_000, 3000)
    hook = make_read_guard_hook(lambda e: None, workspace_root=str(tmp_path))
    for tool_input in ({"file_path": str(normal)}, {"file_path": str(tmp_path / "nope.js")}):
        res = await hook({"tool_name": "Read", "tool_input": tool_input}, None, None)
        assert res == {}


async def test_read_guard_resolves_relative_path_against_workspace_root(tmp_path):
    p = _minified_file(tmp_path)
    hook = make_read_guard_hook(lambda e: None, workspace_root=str(tmp_path))
    res = await hook(
        {"tool_name": "Read", "tool_input": {"file_path": p.name}}, None, None
    )
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_read_guard_ignores_other_tools():
    hook = make_read_guard_hook(lambda e: None)
    res = await hook({"tool_name": "Bash", "tool_input": {"command": "cat x"}}, None, None)
    assert res == {}


# ── read_slice ──


def test_read_slice_pattern_mode_returns_bounded_excerpts(tmp_path):
    p = tmp_path / "b.js"
    needle = b"location.hash"
    p.write_bytes(b"a" * 1000 + needle + b"b" * 1000)

    res = read_slice_impl(str(p), pattern="location.hash", context=50, root=str(tmp_path))
    assert res["matches"], "应命中一处"
    m = res["matches"][0]
    assert m["byte_offset"] == 1000
    assert m["match"] == "location.hash"
    assert len(m["excerpt"].encode()) == 50 + len(needle) + 50
    assert res["capped"] is False


def test_read_slice_pattern_caps_total_output(tmp_path):
    p = tmp_path / "b.js"
    p.write_bytes(b" SecretToken" * 5000)
    res = read_slice_impl(str(p), pattern="SecretToken", context=100, root=str(tmp_path))
    total = sum(len(m["excerpt"].encode()) for m in res["matches"])
    assert total <= READ_SLICE_MAX_OUTPUT
    assert len(res["matches"]) <= READ_SLICE_MAX_MATCHES
    assert res["capped"] is True


def test_read_slice_window_mode_pages(tmp_path):
    p = tmp_path / "b.js"
    p.write_bytes(b"0123456789" * 2560)  # 25600 字节 ASCII

    first = read_slice_impl(str(p), byte_offset=0, byte_length=1024, root=str(tmp_path))
    assert first["byte_offset"] == 0
    assert len(first["excerpt"]) == 1024
    assert first["has_more"] is True

    last = read_slice_impl(str(p), byte_offset=25500, byte_length=4096, root=str(tmp_path))
    assert len(last["excerpt"]) == 100
    assert last["has_more"] is False


def test_read_slice_rejects_paths_outside_root(tmp_path):
    res = read_slice_impl("/etc/passwd", root=str(tmp_path))
    assert "error" in res

    res = read_slice_impl(str(tmp_path / "nope.js"), root=str(tmp_path))
    assert "文件不存在" in res["error"]


def test_read_slice_rejects_invalid_regex(tmp_path):
    p = tmp_path / "b.js"
    p.write_bytes(b"var a=1;")
    res = read_slice_impl(str(p), pattern="(", root=str(tmp_path))
    assert "正则无效" in res["error"]


# ── MCP 工具装配 ──


def test_make_read_slice_tool_delegates_to_impl(tmp_path):
    stub = ModuleType("claude_agent_sdk")

    def _fake_tool(*args, **kwargs):
        def _wrap(fn):
            fn._tool_meta = kwargs
            return fn
        return _wrap

    stub.tool = _fake_tool
    sys.modules["claude_agent_sdk"] = stub

    tool_fn = make_read_slice_tool(workspace_root=str(tmp_path))
    p = tmp_path / "b.js"
    p.write_bytes(b"0123456789" * 100)
    import asyncio

    out = asyncio.run(tool_fn({"file_path": str(p), "byte_offset": 0, "byte_length": 16}))
    assert out["excerpt"] == "0123456789012345"
    assert out["size_bytes"] == 1000
