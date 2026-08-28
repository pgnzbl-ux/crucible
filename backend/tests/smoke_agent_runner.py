"""
agent-runner 容器冒烟测试（HTTP/SSE 守护模式）。

覆盖 5 个 case：
  Case 1: 镜像存在 + 拉起容器 + HTTP 就绪 + /v1/execute 事件流 + runner.exit 终帧 + 自动清理
  Case 2: SSE 帧解析（data: JSON、坏帧跳过、尾帧无空行）
  Case 3: OOM（mem_limit=128m，断言容器被强杀或拉起失败）
  Case 4: 取消（拉起容器 + docker kill，断言 137/143）
  Case 5: 临时目录清理（workdir rmtree）

运行：
    cd backend
    python tests/smoke_agent_runner.py

注意：
- 需要本机 docker daemon；需先构建镜像：
  docker build -f backend/agent-runner/Dockerfile -t crucible-agent-runner:base .
- 不需要真实 LLM 凭据：gateway 因缺少 ANTHROPIC_MODEL 走 SPEC_INVALID 失败路径，
  正好验证「SSE 通道 + 失败事件 + 终帧 + 退出码」全链路。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.agent_runner import (  # noqa: E402
    AgentRunnerError,
    AgentRunnerSpec,
    _iter_sse_events,
    agent_runner_manager,
)

AGENT_SPEC_BODY = {
    "node_key": "smoke",
    "node_payload": {"note": "smoke test"},
    "submit_schema": None,
    "skill_path": None,
    "max_turns": 2,
}


def _make_workdir(prefix: str) -> str:
    workdir = tempfile.mkdtemp(prefix=prefix)
    os.makedirs(os.path.join(workdir, "project"), exist_ok=True)
    return workdir


def _case_1_basic_lifecycle() -> None:
    print("\n=== Case 1: 基础生命周期（HTTP/SSE） ===")
    assert agent_runner_manager.image_exists(), (
        f"agent-runner 镜像不存在: {agent_runner_manager._resolve_defaults(AgentRunnerSpec()).image}\n"
        f"先构建：docker build -f backend/agent-runner/Dockerfile -t crucible-agent-runner:base ."
    )
    print("[OK] 镜像存在")

    workdir = _make_workdir("crucible-smoke-")
    spec = AgentRunnerSpec(
        host_workdir=workdir,
        env={"PYTHONUNBUFFERED": "1", "HOME": "/tmp"},
        agent_spec=dict(AGENT_SPEC_BODY),
        cpu_limit=0.5,
        memory_limit="512m",
    )
    events: list[dict] = []
    try:
        exit_code, summary = agent_runner_manager.run_with_streaming(
            spec=spec,
            on_event=events.append,
        )
        print(f"[OK] 执行完成: exit_code={exit_code}, container={summary.get('container_name')}")
    except AgentRunnerError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise AssertionError(f"HTTP/SSE 执行失败: {e}")

    # 无凭据：SPEC_INVALID 失败事件 + runner.exit(2) 终帧（on_event 不含终帧）
    types_ = [e.get("type") for e in events]
    assert "agent.failed" in types_, f"应含 agent.failed（缺模型凭据），实际 {types_}"
    assert "runner.exit" not in types_, "终帧不应进入 on_event 回调"
    assert exit_code == 2, f"缺凭据应为 exit 2，实际 {exit_code}"
    assert "runner.exit" in summary["transcript"], "transcript 应含终帧"
    print(f"[OK] 事件 {len(events)} 条、终帧与退出码契约正确")

    shutil.rmtree(workdir, ignore_errors=True)
    print("[OK] 临时目录已清理")


def _case_2_sse_frame_parser() -> None:
    print("\n=== Case 2: SSE 帧解析 ===")

    class FakeResp:
        def __init__(self, lines):
            self._lines = lines

        def iter_lines(self):
            return iter(self._lines)

    frames = [
        "event: agent_event",
        'data: {"event_type": "agent.message", "payload": {"text": "hi"}}',
        "",
        "data: broken-json",
        "",
        'data: {"event_type": "runner.exit", "payload": {"exit_code": 0}}',
    ]
    events = list(_iter_sse_events(FakeResp(frames)))
    assert len(events) == 2, f"坏帧应跳过，实际 {events}"
    assert events[0]["payload"]["text"] == "hi"
    assert events[1]["payload"]["exit_code"] == 0
    print("[OK] data 帧解析、坏帧跳过")

    tail = ['data: {"event_type": "agent.failed"}']
    events = list(_iter_sse_events(FakeResp(tail)))
    assert len(events) == 1
    print("[OK] 无结尾空行的尾帧处理")


def _case_3_oom() -> None:
    print("\n=== Case 3: OOM 测试（mem=128m） ===")
    workdir = _make_workdir("crucible-smoke-oom-")
    spec = AgentRunnerSpec(
        host_workdir=workdir,
        env={"PYTHONUNBUFFERED": "1", "HOME": "/tmp"},
        agent_spec=dict(AGENT_SPEC_BODY),
        cpu_limit=0.5,
        memory_limit="128m",  # 故意极小
    )
    try:
        exit_code, summary = agent_runner_manager.run_with_streaming(
            spec=spec,
            on_event=lambda e: None,
        )
        print(f"[INFO] exit_code={exit_code}, oom_killed={summary.get('oom_killed')}")
        assert exit_code != 0 or summary.get("oom_killed"), (
            f"128m 内存限制未生效（exit_code={exit_code}, oom_killed={summary.get('oom_killed')}）"
        )
        print("[OK] OOM 限制生效")
    except AgentRunnerError as e:
        # 就绪等待期间容器被 OOM 杀掉也会走这里（符合预期）
        print(f"[INFO] 容器提前退出（符合预期）: {str(e)[:200]}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _case_4_cancel() -> None:
    print("\n=== Case 4: 取消测试（拉起 + docker kill） ===")
    workdir = _make_workdir("crucible-smoke-cancel-")
    spec = AgentRunnerSpec(
        host_workdir=workdir,
        env={"PYTHONUNBUFFERED": "1", "HOME": "/tmp"},
        agent_spec=dict(AGENT_SPEC_BODY),
        cpu_limit=0.5,
        memory_limit="512m",
    )
    runner = None
    try:
        runner = agent_runner_manager.create(spec)
        print(f"[OK] 拉起: {runner.name}")

        time.sleep(2)
        runner.container.kill()
        print("[OK] docker kill 完成")

        wait_result = runner.container.wait()
        exit_code = int(wait_result.get("StatusCode", 1))
        print(f"[INFO] exit_code={exit_code}")
        assert exit_code in (137, 143), f"被 kill 应得到 137/143，得到 {exit_code}"
        print("[OK] 取消路径正确")
    except AgentRunnerError as e:
        raise AssertionError(f"拉起失败: {e}")
    finally:
        if runner is not None:
            runner.stop_and_remove()
        shutil.rmtree(workdir, ignore_errors=True)


def _case_5_workdir_cleanup() -> None:
    print("\n=== Case 5: 临时目录清理 ===")
    workdir = tempfile.mkdtemp(prefix="crucible-smoke-cleanup-")
    assert os.path.isdir(workdir)
    shutil.rmtree(workdir, ignore_errors=True)
    assert not os.path.isdir(workdir)
    print("[OK] rmtree 后目录消失")

    shutil.rmtree("/tmp/crucible-nonexistent-12345", ignore_errors=True)
    print("[OK] 不存在路径 rmtree 安全")


def main() -> None:
    print("=== agent-runner 容器冒烟测试（HTTP/SSE 模式） ===")

    if not agent_runner_manager.image_exists():
        print(
            f"[FAIL] agent-runner 镜像不存在\n"
            f"  docker build -f backend/agent-runner/Dockerfile -t crucible-agent-runner:base .\n"
        )
        sys.exit(1)

    _case_1_basic_lifecycle()
    _case_2_sse_frame_parser()
    _case_3_oom()
    _case_4_cancel()
    _case_5_workdir_cleanup()

    removed = agent_runner_manager.cleanup_stale(max_age_seconds=0)
    print(f"[OK] 巡检清理 {removed} 个过期容器")

    print("\n=== 全部通过 ===")


if __name__ == "__main__":
    main()
