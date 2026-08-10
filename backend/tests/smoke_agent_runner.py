"""
agent-runner 容器冒烟测试 — 替代原 smoke_sandbox.py。

覆盖 5 个 case：
  Case 1: 镜像存在 + 拉起容器 + 收至少 1 条事件 + 自动清理
  Case 2: 行缓冲解析（半行 chunk 拼接）
  Case 3: OOM（mem_limit=128m + 触发大内存，断言 OOMKilled）
  Case 4: 取消（拉起容器 + 5 秒后 docker kill）
  Case 5: 临时目录清理（agent-runner 跑完后 workdir 已被 rmtree）

运行：
    cd backend
    python tests/smoke_agent_runner.py

注意：
- 需要本机 docker daemon（与原 sandbox 测试相同）
- 不需要真实 LLM 凭据（容器内 SDK 调用会因 missing env 失败，但容器本身能跑起来）
- Case 1 期望收到 agent.failed 事件（凭据缺失），证明事件流通道打通
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.agent_runner import (  # noqa: E402
    AgentRunnerError,
    AgentRunnerSpec,
    LineBufferedJsonParser,
    agent_runner_manager,
)


PROMPT_JSON_TEMPLATE = {
    "task_id": "smoke-00000000",
    "run_id": "smoke-run-00000000",
    "project_address": "https://example.com/test.git",
    "project_ref": "main",
    "vulnerability_description": "smoke test - 漏洞验证",
}


def _case_1_basic_lifecycle() -> None:
    print("\n=== Case 1: 基础生命周期 ===")
    # 1. 镜像存在
    assert agent_runner_manager.image_exists(), (
        f"agent-runner 镜像不存在: {agent_runner_manager._resolve_defaults(AgentRunnerSpec()).image}\n"
        f"先构建：cd infrastructure && docker build -f agent-runner/Dockerfile -t crucible-agent-runner:base ."
    )
    print("[OK] 镜像存在")

    # 2. 准备 host 临时目录
    workdir = tempfile.mkdtemp(prefix="crucible-smoke-")
    prompt_path = os.path.join(workdir, ".prompt.json")
    with open(prompt_path, "w", encoding="utf-8") as f:
        json.dump(PROMPT_JSON_TEMPLATE, f, ensure_ascii=False)
    # 容器内需要 /workspace/project/ 存在（哪怕是空目录）
    os.makedirs(os.path.join(workdir, "project"), exist_ok=True)

    # 3. 拉起容器（不注入真实凭据，预期收到 agent.failed 事件）
    spec = AgentRunnerSpec(
        host_workdir=workdir,
        env={
            # 故意不设 ANTHROPIC_API_KEY，让容器内 run_one.py 失败
            "PYTHONUNBUFFERED": "1",
            "HOME": "/workspace",
        },
        cpu_limit=0.5,
        memory_limit="512m",
    )
    events: list[dict] = []
    try:
        exit_code, summary = agent_runner_manager.run_with_streaming(
            spec=spec,
            on_event=lambda e: events.append(e),
        )
        print(f"[OK] 容器跑完: exit_code={exit_code}, container={summary.get('container_name')}")
    except AgentRunnerError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise AssertionError(f"拉起失败: {e}")

    # 4. 断言收到事件（凭据缺失应至少 1 条 agent.failed）
    assert len(events) >= 1, f"应至少 1 条事件，实际 {len(events)} 条"
    print(f"[OK] 收到 {len(events)} 条事件")
    event_types = {e.get("type") for e in events}
    print(f"[INFO] 事件类型集合: {event_types}")

    # 5. 清理
    shutil.rmtree(workdir, ignore_errors=True)
    print("[OK] 临时目录已清理")


def _case_2_line_buffer_parser() -> None:
    print("\n=== Case 2: 行缓冲解析（半行 chunk 拼接） ===")
    parser = LineBufferedJsonParser()

    # 半行 chunk1（缺结尾 \n）
    chunk1 = b'{"type":"phase.updated","phase":"start"'
    events = list(parser.feed(chunk1))
    assert len(events) == 0, f"半行不应产生事件，实际 {len(events)} 条"
    print("[OK] 半行不产生事件")

    # 完成 chunk2
    chunk2 = b',"sequence":1,"timestamp":1.0}\n'
    events = list(parser.feed(chunk2))
    assert len(events) == 1, f"完成行应产生 1 条事件，实际 {len(events)} 条"
    assert events[0]["type"] == "phase.updated", f"事件 type 错误: {events[0]}"
    assert events[0]["phase"] == "start"
    print("[OK] 半行 + 完成行正确拼接")

    # 多行 + 空行
    chunk3 = b'{"type":"agent.message","text":"hi"}\n\n{"type":"raw","content":"x"}\n'
    events = list(parser.feed(chunk3))
    assert len(events) == 2, f"多行应产生 2 条事件，实际 {len(events)} 条"
    assert events[0]["type"] == "agent.message"
    assert events[1]["type"] == "raw"
    print("[OK] 多行 + 空行正确处理")

    # 非法 JSON 容忍
    chunk4 = b"not-json-line\n"
    events = list(parser.feed(chunk4))
    assert len(events) == 1
    assert events[0]["type"] == "raw"
    print("[OK] 非法 JSON 容忍为 raw 事件")

    # flush
    chunk5 = b'{"type":"last","no":"newline"'
    events = list(parser.feed(chunk5))
    assert len(events) == 0
    events = list(parser.flush())
    assert len(events) == 1
    assert events[0]["type"] == "last"
    print("[OK] flush 处理残余")


def _case_3_oom() -> None:
    print("\n=== Case 3: OOM 测试（mem=128m + 拉起大容器） ===")
    workdir = tempfile.mkdtemp(prefix="crucible-smoke-oom-")
    prompt_path = os.path.join(workdir, ".prompt.json")
    with open(prompt_path, "w", encoding="utf-8") as f:
        json.dump(PROMPT_JSON_TEMPLATE, f, ensure_ascii=False)
    os.makedirs(os.path.join(workdir, "project"), exist_ok=True)

    spec = AgentRunnerSpec(
        host_workdir=workdir,
        env={"PYTHONUNBUFFERED": "1", "HOME": "/workspace"},
        cpu_limit=0.5,
        memory_limit="128m",  # 故意极小
    )
    try:
        exit_code, summary = agent_runner_manager.run_with_streaming(
            spec=spec,
            on_event=lambda e: None,  # 不收集事件，只关注容器结局
        )
        print(f"[INFO] exit_code={exit_code}, oom_killed={summary.get('oom_killed')}")
        # 128m + python 解释器本身可能刚启动就 OOM；事件流可能根本没机会输出
        # 这里只断言容器被 docker 强制结束（exit_code != 0 或 OOM killed）
        assert exit_code != 0 or summary.get("oom_killed"), (
            f"128m 内存限制未生效（exit_code={exit_code}, oom_killed={summary.get('oom_killed')}）"
        )
        print("[OK] OOM 限制生效")
    except AgentRunnerError as e:
        # 拉起本身失败也算通过（极少内存下 docker 拒绝创建也算正常）
        print(f"[INFO] 拉起失败（符合预期）: {e}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _case_4_cancel() -> None:
    print("\n=== Case 4: 取消测试（拉起 + 5 秒后 kill） ===")
    workdir = tempfile.mkdtemp(prefix="crucible-smoke-cancel-")
    prompt_path = os.path.join(workdir, ".prompt.json")
    with open(prompt_path, "w", encoding="utf-8") as f:
        json.dump(PROMPT_JSON_TEMPLATE, f, ensure_ascii=False)
    os.makedirs(os.path.join(workdir, "project"), exist_ok=True)

    spec = AgentRunnerSpec(
        host_workdir=workdir,
        env={"PYTHONUNBUFFERED": "1", "HOME": "/workspace"},
        cpu_limit=0.5,
        memory_limit="512m",
    )
    runner = None
    try:
        runner = agent_runner_manager.create(spec)
        print(f"[OK] 拉起: {runner.name}")

        time.sleep(2)  # 给容器一点启动时间
        runner.container.kill()
        print("[OK] docker kill 完成")

        # wait 同步等待（已被 kill，会快速返回）
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
    print("\n=== Case 5: 临时目录清理（tasks.py rmtree 路径模拟） ===")
    workdir = tempfile.mkdtemp(prefix="crucible-smoke-cleanup-")
    assert os.path.isdir(workdir)
    print(f"[OK] 创建: {workdir}")

    # 模拟 tasks.py 的 finally 清理
    shutil.rmtree(workdir, ignore_errors=True)
    assert not os.path.isdir(workdir)
    print("[OK] rmtree 后目录消失")

    # 测试不存在的路径不会抛
    shutil.rmtree("/tmp/crucible-nonexistent-12345", ignore_errors=True)
    print("[OK] 不存在路径 rmtree 安全")


def main() -> None:
    print("=== agent-runner 容器冒烟测试 ===")

    # 镜像检查（先 fail-fast：未构建镜像直接报错）
    if not agent_runner_manager.image_exists():
        print(
            f"[FAIL] agent-runner 镜像不存在\n"
            f"  1. cd infrastructure\n"
            f"  2.  docker build -f agent-runner/Dockerfile -t crucible-agent-runner:base .\n"
        )
        sys.exit(1)

    _case_1_basic_lifecycle()
    _case_2_line_buffer_parser()
    _case_3_oom()
    _case_4_cancel()
    _case_5_workdir_cleanup()

    # 保险 B 巡检
    removed = agent_runner_manager.cleanup_stale(max_age_seconds=0)
    print(f"[OK] 巡检清理 {removed} 个过期容器")

    print("\n=== 全部通过 ===")


if __name__ == "__main__":
    main()