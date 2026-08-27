"""run_one.py Stop hook：会话欲结束但未 submit_result 时催交一次。"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "infrastructure",
        "agent-runner",
    ),
)

sys.modules.setdefault("claude_agent_sdk", MagicMock())

from runner.run_one import _stop_hook  # noqa: E402


@pytest.mark.asyncio
async def test_stop_hook_blocks_when_node_output_missing(tmp_path, monkeypatch, capsys):
    """节点模式且无 .node_output.json 时，必须 block 并点名 submit_result。"""
    out = tmp_path / "node_output.json"
    monkeypatch.setenv("NODE_KEY", "reproduce")
    monkeypatch.setenv("NODE_OUTPUT_PATH", str(out))

    result = await _stop_hook({"stop_hook_active": False}, None, None)

    assert result["decision"] == "block"
    assert "submit_result" in result["reason"]
    logged = capsys.readouterr().out
    event = json.loads(logged.strip().splitlines()[-1])
    assert event["type"] == "phase.updated"
    assert "submit_result" in event["message"]


@pytest.mark.asyncio
async def test_stop_hook_allows_when_node_output_exists(tmp_path, monkeypatch):
    out = tmp_path / "node_output.json"
    out.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NODE_KEY", "reproduce")
    monkeypatch.setenv("NODE_OUTPUT_PATH", str(out))

    assert await _stop_hook({"stop_hook_active": False}, None, None) == {}


@pytest.mark.asyncio
async def test_stop_hook_allows_after_one_nudge(tmp_path, monkeypatch):
    """stop_hook_active=true 表示已经催过一轮，禁止无限 block 烧钱。"""
    out = tmp_path / "node_output.json"
    monkeypatch.setenv("NODE_KEY", "reproduce")
    monkeypatch.setenv("NODE_OUTPUT_PATH", str(out))

    assert await _stop_hook({"stop_hook_active": True}, None, None) == {}


@pytest.mark.asyncio
async def test_stop_hook_noop_without_node_key(tmp_path, monkeypatch):
    monkeypatch.delenv("NODE_KEY", raising=False)
    monkeypatch.setenv("NODE_OUTPUT_PATH", str(tmp_path / "missing.json"))

    assert await _stop_hook({"stop_hook_active": False}, None, None) == {}
