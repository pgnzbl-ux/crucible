"""run_one.py 压缩产物治理：Read/Grep 拦截钩子 + read_slice 有界读取。"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

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

from runner import run_one  # noqa: E402 — 需先注入 sys.path 与 SDK stub


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


# ── _is_minified_file ──


def test_is_minified_file_classifies_by_size_and_lines(tmp_path):
    hit, size, lines = run_one._is_minified_file(_minified_file(tmp_path))
    assert (hit, size, lines) == (True, 400_000, 3)

    normal = tmp_path / "normal.js"
    _write_sized(normal, 400_000, 3000)
    hit, _, _ = run_one._is_minified_file(normal)
    assert hit is False

    assert run_one._is_minified_file(tmp_path / "nope.js")[0] is False


# ── _read_guard_hook ──


async def test_read_guard_denies_read_on_minified(tmp_path, capsys):
    p = _minified_file(tmp_path)
    res = await run_one._read_guard_hook(
        {"tool_name": "Read", "tool_input": {"file_path": str(p), "offset": 280, "limit": 15}},
        None,
        None,
    )
    decision = res["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    reason = decision["permissionDecisionReason"]
    assert "read_slice" in reason
    assert "不要重试 Read" in reason
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["type"] == "tool.call.denied"
    assert event["tool"] == "Read"
    assert "minified artifact" in event["reason"]


async def test_read_guard_denies_grep_on_minified(tmp_path):
    p = _minified_file(tmp_path)
    res = await run_one._read_guard_hook(
        {"tool_name": "Grep", "tool_input": {"pattern": "eval", "path": str(p)}},
        None,
        None,
    )
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_read_guard_allows_normal_and_missing_targets(tmp_path):
    normal = tmp_path / "normal.js"
    _write_sized(normal, 400_000, 3000)
    for tool_input in ({"file_path": str(normal)}, {"file_path": str(tmp_path / "nope.js")}):
        res = await run_one._read_guard_hook(
            {"tool_name": "Read", "tool_input": tool_input}, None, None
        )
        assert res == {}


async def test_read_guard_resolves_relative_path_against_workspace_root(tmp_path, monkeypatch):
    p = _minified_file(tmp_path)
    monkeypatch.setattr(run_one, "_WORKSPACE_ROOT", str(tmp_path))
    res = await run_one._read_guard_hook(
        {"tool_name": "Read", "tool_input": {"file_path": p.name}}, None, None
    )
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_read_guard_ignores_other_tools(tmp_path):
    res = await run_one._read_guard_hook(
        {"tool_name": "Bash", "tool_input": {"command": "cat x"}}, None, None
    )
    assert res == {}


# ── read_slice ──


def test_read_slice_pattern_mode_returns_bounded_excerpts(tmp_path):
    p = tmp_path / "b.js"
    needle = b"location.hash"
    p.write_bytes(b"a" * 1000 + needle + b"b" * 1000)

    res = run_one._read_slice_impl(
        str(p), pattern="location.hash", context=50, root=str(tmp_path)
    )
    assert res["matches"], "应命中一处"
    m = res["matches"][0]
    assert m["byte_offset"] == 1000
    assert m["match"] == "location.hash"
    assert len(m["excerpt"].encode()) == 50 + len(needle) + 50
    assert res["capped"] is False


def test_read_slice_pattern_caps_total_output(tmp_path):
    p = tmp_path / "b.js"
    p.write_bytes(b" SecretToken" * 5000)
    res = run_one._read_slice_impl(
        str(p), pattern="SecretToken", context=100, root=str(tmp_path)
    )
    total = sum(len(m["excerpt"].encode()) for m in res["matches"])
    assert total <= run_one._READ_SLICE_MAX_OUTPUT
    assert len(res["matches"]) <= run_one._READ_SLICE_MAX_MATCHES
    assert res["capped"] is True


def test_read_slice_window_mode_pages(tmp_path):
    p = tmp_path / "b.js"
    p.write_bytes(b"0123456789" * 2560)  # 25600 字节 ASCII

    first = run_one._read_slice_impl(str(p), byte_offset=0, byte_length=1024, root=str(tmp_path))
    assert first["byte_offset"] == 0
    assert len(first["excerpt"]) == 1024
    assert first["has_more"] is True

    last = run_one._read_slice_impl(str(p), byte_offset=25500, byte_length=4096, root=str(tmp_path))
    assert len(last["excerpt"]) == 100
    assert last["has_more"] is False


def test_read_slice_rejects_paths_outside_root(tmp_path):
    res = run_one._read_slice_impl("/etc/passwd", root=str(tmp_path))
    assert "error" in res

    res = run_one._read_slice_impl(str(tmp_path / "nope.js"), root=str(tmp_path))
    assert "文件不存在" in res["error"]


def test_read_slice_rejects_invalid_regex(tmp_path):
    p = tmp_path / "b.js"
    p.write_bytes(b"var a=1;")
    res = run_one._read_slice_impl(str(p), pattern="(", root=str(tmp_path))
    assert "正则无效" in res["error"]


# ── 注册 ──


def test_build_options_registers_read_guard_and_slice(monkeypatch):
    from dataclasses import dataclass, field
    from pathlib import Path

    captured: dict = {}

    class CaptureOptions:
        def __init__(self, **kwargs):
            captured.clear()
            captured.update(kwargs)

    # 其他 run_one 测试会把 run_one.HookMatcher 换成 fake 且不还原，
    # 这里同样换成自己的 fake（有 .matcher 属性），直接检查捕获到的注册结果
    @dataclass
    class FakeHookMatcher:
        matcher: str | None = None
        hooks: list = field(default_factory=list)

    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv(
        "NODE_SKILL_DIR",
        str(root / "backend" / "agent-runner" / "node-skills"),
    )
    run_one.ClaudeAgentOptions = CaptureOptions
    run_one.HookMatcher = FakeHookMatcher

    run_one._build_options(model="m", max_turns=5, node_key="triage")

    by_matcher = {m.matcher: m for m in captured["hooks"]["PreToolUse"]}
    assert by_matcher["Bash"].hooks == [run_one._pre_tool_use_hook]
    assert by_matcher["Read"].hooks == [run_one._read_guard_hook]
    assert by_matcher["Grep"].hooks == [run_one._read_guard_hook]
    assert "mcp__crucible__read_slice" in captured["allowed_tools"]
