"""runner Stop hook：会话欲结束但未 submit_result 时催交一次。"""

import os
import sys
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


from runner.policies import make_stop_hook  # noqa: E402


@pytest.mark.asyncio
async def test_stop_hook_blocks_when_output_missing(tmp_path, capsys):
    """有提交契约且无产出文件时，必须 block 并点名 submit_result。"""
    out = tmp_path / "node_output.json"
    events: list[dict] = []
    hook = make_stop_hook(events.append, output_path=str(out))

    result = await hook({"stop_hook_active": False}, None, None)

    assert result["decision"] == "block"
    assert "submit_result" in result["reason"]
    assert len(events) == 1
    assert events[0]["type"] == "phase.updated"
    assert "submit_result" in events[0]["message"]


@pytest.mark.asyncio
async def test_stop_hook_allows_when_output_exists(tmp_path):
    out = tmp_path / "node_output.json"
    out.write_text("{}", encoding="utf-8")
    hook = make_stop_hook(lambda e: None, output_path=str(out))

    assert await hook({"stop_hook_active": False}, None, None) == {}


@pytest.mark.asyncio
async def test_stop_hook_allows_after_one_nudge(tmp_path):
    """stop_hook_active=true 表示已经催过一轮，禁止无限 block 烧钱。"""
    out = tmp_path / "node_output.json"
    hook = make_stop_hook(lambda e: None, output_path=str(out))

    assert await hook({"stop_hook_active": True}, None, None) == {}


@pytest.mark.asyncio
async def test_stop_hook_not_registered_without_contract(tmp_path):
    """无提交契约时网关不注册 Stop hook（由 build_policy_hooks 控制）。"""
    import types
    from unittest.mock import MagicMock

    from runner.policies import build_policy_hooks

    stub = types.ModuleType("claude_agent_sdk")
    stub.HookMatcher = MagicMock()
    sys.modules["claude_agent_sdk"] = stub

    hooks = build_policy_hooks(
        lambda e: None, workspace_root="/workspace",
        output_path=str(tmp_path / "missing.json"), submit_enforced=False,
    )
    assert "Stop" not in hooks
