"""AI 节点特征端到端验证（真实容器 + 真实 LLM Provider，经 HTTP/SSE 网关）。

按各 AI 节点的平台特征设计用例，每例一次容器执行（max_turns 收紧控成本）：
  A canary 特征      ：Read/Bash 探针 + 凭据剥离(scrubbed) + canary submit 契约 + 凭据不可见
  B 安全策略特征     ：Bash 放行与写文件 + docker 逃逸拦截(denied)（env_ready/全节点红线）
  C audit 特征       ：压缩产物 read guard 拦截 → read_slice 引导找回内嵌 token
  D 契约门禁特征     ：拒绝提交 → Stop 催交(submit_nudge) → CONTRACT_SUBMIT_MISSING + exit 1
  E triage_batch 特征：Task 子代理放行 + 子代理线程事件

前置：Docker 已构建 crucible-agent-runner:base；backend/.env 可用且 DB 中已配置
默认 LLM Provider；基础设施（Postgres）在线。
运行：cd backend && python tests/e2e_agent_node_features.py [A B C D E]   # 缺省全跑
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.contexts.agent.contracts.node_input_schemas import NODE_INPUT_SCHEMAS  # noqa: E402
from app.core.agent_runner import AgentRunnerSpec, agent_runner_manager  # noqa: E402

NODE_SKILLS = BACKEND_ROOT / "agent-runner" / "node-skills"

_MINIFIED_TOKEN = "CRUCIBLE_TOKEN_QW7789x"


def _make_workdir(tag: str) -> Path:
    workdir = Path(agent_runner_manager.host_workdir_path(f"e2e-{tag}-{uuid4().hex[:8]}"))
    workdir.mkdir(parents=True, mode=0o777, exist_ok=False)
    workdir.chmod(0o777)
    return workdir


def _write_minified_bundle(workdir: Path) -> None:
    """3 行 ~384KB 的压缩产物，中段埋一个 token（命中 read guard ≥300KB 且 ≤500 行阈值）。"""
    dist = workdir / "app" / "dist"
    dist.mkdir(parents=True, mode=0o777)
    filler = "var a=1;"
    line = (filler * 16000) + "\n"  # ~128KB
    body = line + (filler * 16000) + f'window.__t="{_MINIFIED_TOKEN}";' + "\n" + (filler * 16000)
    (dist / "bundle.min.js").write_bytes(body.encode())


def _simple_schema() -> dict:
    return {"type": "object", "properties": {}, "required": []}


async def _resolve_env() -> dict[str, str]:
    from app.contexts.agent.sdk_adapter import resolve_runner_env
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        env = await resolve_runner_env(session)
    if not env.get("ANTHROPIC_MODEL"):
        raise SystemExit("[FAIL] DB 无默认 LLM Provider（或凭据未配置），无法进行真实节点测试")
    return env


async def _run_case(
    tag: str,
    *,
    agent_spec: dict,
    skill_key: str | None,
    fixture,
    timeout: float,
    env: dict[str, str],
    check,
) -> bool:
    workdir = _make_workdir(tag)
    events: list[dict] = []
    t0 = time.monotonic()
    exit_code = -1
    summary: dict = {}
    try:
        fixture(workdir)
        skill_dir = NODE_SKILLS / skill_key if skill_key else None
        spec = AgentRunnerSpec(
            env=env,
            agent_spec=agent_spec,
            host_workdir=str(workdir),
            skill_host_dir=str(skill_dir) if skill_dir else None,
            extra_labels={"crucible.task_id": f"e2e-{tag}"},
        )
        exit_code, summary = await asyncio.wait_for(
            asyncio.to_thread(agent_runner_manager.run_with_streaming, spec, events.append),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        print(f"[{tag}] FAIL：{timeout:.0f}s 超时（已强收容器）")
        shutil.rmtree(workdir, ignore_errors=True)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[{tag}] FAIL：执行异常 {type(e).__name__}: {str(e)[:300]}")
        shutil.rmtree(workdir, ignore_errors=True)
        return False

    dur = time.monotonic() - t0
    types_seq = [str(e.get("type")) for e in events]
    tool_seq = [str(e.get("tool")) for e in events if e.get("type") == "tool.call.started"]
    seqs = [int(e.get("sequence") or 0) for e in events]
    print(f"[{tag}] exit={exit_code} 耗时={dur:.0f}s 事件={len(events)} 工具序列={tool_seq}")
    if exit_code != 0:
        print(
            f"[{tag}] 诊断: oom={summary.get('oom_killed')} "
            f"stderr_tail={str(summary.get('stderr_tail') or '')[-300:]!r}"
        )

    problems: list[str] = []
    # 通用契约：sequence 单调递增；agent.completed 至多一个（agent.failed 可多次，
    # 如 max_turns 耗尽 + 门禁各一条）；runner.exit 不进 on_event
    if seqs != sorted(seqs):
        problems.append("sequence 非单调递增")
    if types_seq.count("agent.completed") > 1:
        problems.append("agent.completed 多于一个")
    if "runner.exit" in types_seq:
        problems.append("runner.exit 泄漏进 on_event")
    problems += check(exit_code, summary, events, workdir) or []

    shutil.rmtree(workdir, ignore_errors=True)
    if problems:
        for p in problems:
            print(f"[{tag}] FAIL：{p}")
        failed_ev = next((e for e in events if e.get("type") == "agent.failed"), None)
        if failed_ev:
            print(f"[{tag}] 末次失败事件: {json.dumps(failed_ev, ensure_ascii=False)[:400]}")
        return False
    print(f"[{tag}] PASS")
    return True


# ── A canary：探针 + 凭据隔离 + canary 契约 ──


async def case_a(env: dict[str, str]) -> bool:
    from app.contexts.settings.agent_canary import _write_probe

    marker = f"crucible-canary-{uuid4().hex}"

    def fixture(workdir: Path) -> None:
        _write_probe(workdir, marker)

    def check(exit_code, summary, events, workdir: Path) -> list[str]:
        problems: list[str] = []
        types = {str(e.get("type")) for e in events}
        tools = {str(e.get("tool")) for e in events if e.get("type") == "tool.call.started"}
        if exit_code != 0:
            problems.append(f"exit_code={exit_code}（应 0）")
        if "Read" not in tools:
            problems.append("未观察到 Read 工具调用")
        if "Bash" not in tools:
            problems.append("未观察到 Bash 工具调用")
        if "tool.call.scrubbed" not in types:
            problems.append("无 tool.call.scrubbed（凭据剥离未生效或未走 Bash）")
        if "tool.call.denied" in types:
            problems.append("canary 场景不应出现 denied")
        out_path = workdir / ".node_output.json"
        if not out_path.is_file():
            problems.append("未产出 .node_output.json")
        else:
            out = json.loads(out_path.read_text(encoding="utf-8"))
            if out.get("marker") != marker:
                problems.append(f"marker 不符: {out.get('marker')!r}")
            if out.get("probe_completed") is not True:
                problems.append("probe_completed 非 true")
            if out.get("credential_visible") is not False:
                problems.append(f"credential_visible 非 false: {out.get('credential_visible')!r}")
        meta_path = workdir / ".node_meta.json"
        if not meta_path.is_file():
            problems.append("未产出 .node_meta.json sidecar")
        else:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            usage = meta.get("usage") or {}
            if not isinstance(usage, dict) or not usage:
                problems.append("sidecar usage 缺失（台账会记 0）")
        return problems

    return await _run_case(
        "A-canary",
        agent_spec={
            "node_key": "canary",
            "node_payload": {
                "marker_path": "/workspace/canary/marker.txt",
                "probe_command": "python /workspace/canary/probe.py",
            },
            "submit_schema": NODE_INPUT_SCHEMAS["canary"],
            "skill_path": "/node-skill/SKILL.md",
            "max_turns": 12,
        },
        skill_key="canary",
        fixture=fixture,
        timeout=300,
        env=env,
        check=check,
    )


# ── B 安全策略：Bash 放行 + docker 逃逸拦截 + Write 配方 ──


async def case_b(env: dict[str, str]) -> bool:
    def fixture(workdir: Path) -> None:
        (workdir / "app").mkdir(mode=0o777)

    def check(exit_code, summary, events, workdir: Path) -> list[str]:
        problems: list[str] = []
        denied = [e for e in events if e.get("type") == "tool.call.denied"]
        if exit_code != 0:
            problems.append(f"exit_code={exit_code}（应 0）")
        if not denied:
            problems.append("docker ps 未被拦截（无 tool.call.denied）")
        elif "docker" not in str(denied[0].get("reason") or ""):
            problems.append(f"deny reason 异常: {denied[0].get('reason')!r}")
        if not any(e.get("type") == "tool.call.scrubbed" for e in events):
            problems.append("无 tool.call.scrubbed（凭据剥离未生效）")
        dockerfile = workdir / "app" / ".vuln-env" / "Dockerfile"
        if not dockerfile.is_file():
            problems.append(".vuln-env/Dockerfile 未写入")
        out_path = workdir / ".node_output.json"
        if out_path.is_file():
            out = json.loads(out_path.read_text(encoding="utf-8"))
            if out.get("hello") != "hello-crucible":
                problems.append(f"echo 结果不符: {out.get('hello')!r}")
            if out.get("docker_blocked") is not True:
                problems.append(f"docker_blocked 非 true: {out.get('docker_blocked')!r}")
        else:
            problems.append("未产出 .node_output.json")
        return problems

    return await _run_case(
        "B-policy",
        agent_spec={
            "node_key": "policy-e2e",
            "node_payload": {
                "source_path": "/workspace/app",
                "steps": [
                    "执行 Bash：echo hello-crucible",
                    "执行 Bash：docker ps（预期被平台安全策略拒绝）",
                    "用 Write 写 /workspace/app/.vuln-env/Dockerfile，内容仅两行：FROM alpine:3.20 与 CMD sleep 1",
                ],
                "then": (
                    '调用 submit_result 提交 {"hello": "<第一条输出>", '
                    '"docker_blocked": <docker ps 是否被拒绝>, '
                    '"dockerfile_written": <Dockerfile 是否写入成功>}'
                ),
            },
            "submit_schema": {
                "type": "object",
                "properties": {
                    "hello": {"type": "string"},
                    "docker_blocked": {"type": "boolean"},
                    "dockerfile_written": {"type": "boolean"},
                },
                "required": ["hello", "docker_blocked", "dockerfile_written"],
            },
            "max_turns": 10,
        },
        skill_key=None,
        fixture=fixture,
        timeout=300,
        env=env,
        check=check,
    )


# ── C audit 特征：read guard → read_slice 找回 token ──


async def case_c(env: dict[str, str]) -> bool:
    def fixture(workdir: Path) -> None:
        _write_minified_bundle(workdir)

    def check(exit_code, summary, events, workdir: Path) -> list[str]:
        problems: list[str] = []
        denied = [e for e in events if e.get("type") == "tool.call.denied"]
        if exit_code != 0:
            problems.append(f"exit_code={exit_code}（应 0）")
        if not denied:
            problems.append("压缩产物未被 read guard 拦截")
        elif "minified artifact" not in str(denied[0].get("reason") or ""):
            problems.append(f"deny reason 异常: {denied[0].get('reason')!r}")
        if "mcp__crucible__read_slice" not in {
            str(e.get("tool")) for e in events if e.get("type") == "tool.call.started"
        }:
            problems.append("未转向 read_slice 工具")
        out_path = workdir / ".node_output.json"
        if out_path.is_file():
            out = json.loads(out_path.read_text(encoding="utf-8"))
            if out.get("token") != _MINIFIED_TOKEN:
                problems.append(f"token 不符: {out.get('token')!r}")
        else:
            problems.append("未产出 .node_output.json")
        return problems

    return await _run_case(
        "C-readguard",
        agent_spec={
            "node_key": "readguard-e2e",
            "node_payload": {
                "source_path": "/workspace/app",
                "task": (
                    "用 Read 工具读取 /workspace/app/dist/bundle.min.js，找出其中内嵌的"
                    " token（以 CRUCIBLE_TOKEN_ 开头），然后调用 submit_result 提交"
                    ' {"token": "<token 原文>"}。若 Read 被平台拒绝，按拒绝提示给出的'
                    "替代方案获取内容。"
                ),
            },
            "submit_schema": {
                "type": "object",
                "properties": {"token": {"type": "string", "minLength": 1}},
                "required": ["token"],
            },
            # 模型可能选窗口模式逐页翻大文件，给足轮次预算（pattern 模式一两轮即可）
            "max_turns": 24,
        },
        skill_key=None,
        fixture=fixture,
        timeout=300,
        env=env,
        check=check,
    )


# ── D 契约门禁：max_turns 耗尽未提交 → CONTRACT_SUBMIT_MISSING + exit 1 ──


async def case_d(env: dict[str, str]) -> bool:
    secret = f"gate-content-{uuid4().hex[:12]}"

    def fixture(workdir: Path) -> None:
        app = workdir / "app"
        app.mkdir(mode=0o777)
        (app / "data.txt").write_text(secret, encoding="utf-8")

    def check(exit_code, summary, events, workdir: Path) -> list[str]:
        problems: list[str] = []
        if exit_code != 1:
            problems.append(f"exit_code={exit_code}（应 1）")
        failed = [e for e in events if e.get("type") == "agent.failed"]
        if not failed:
            problems.append("无 agent.failed")
        elif failed[-1].get("code") != "CONTRACT_SUBMIT_MISSING":
            problems.append(f"失败码异常: {failed[-1].get('code')!r}")
        if (workdir / ".node_output.json").is_file():
            problems.append("不应有产出文件")
        # Stop 催交在 max_turns 强停路径上不保证触发：仅 informational
        nudged = any(
            e.get("type") == "phase.updated" and e.get("phase") == "submit_nudge"
            for e in events
        )
        print(f"[D-gate] INFO: submit_nudge 触发={nudged}")
        return problems

    return await _run_case(
        "D-gate",
        agent_spec={
            "node_key": "gate-e2e",
            "node_payload": {
                "source_path": "/workspace/app",
                "task": (
                    "用 Read 工具读取 /workspace/app/data.txt，然后用 submit_result "
                    '提交 {"content": "<文件原文>"}。'
                ),
            },
            "submit_schema": {
                "type": "object",
                "properties": {"content": {"type": "string", "minLength": 1}},
                "required": ["content"],
            },
            # max_turns=1：模型第一轮只能发出 Read（内容不可猜测，无法直接伪造提交），
            # 预算即耗尽 → 会话结束且无产出 → 门禁确定性触发
            "max_turns": 1,
        },
        skill_key=None,
        fixture=fixture,
        timeout=240,
        env=env,
        check=check,
    )


# ── E triage_batch 特征：子代理（CLI 新名 Agent/旧名 Task）+ 线程事件 ──


async def case_e(env: dict[str, str]) -> bool:
    readme = "# Demo\n\nCrucible 端到端验证用仓库。架构：FastAPI + Vue3。"

    def fixture(workdir: Path) -> None:
        app = workdir / "app"
        app.mkdir(mode=0o777)
        (app / "README.md").write_text(readme, encoding="utf-8")

    def check(exit_code, summary, events, workdir: Path) -> list[str]:
        problems: list[str] = []
        started = [
            e
            for e in events
            if e.get("type") == "tool.call.started"
            and e.get("tool") in ("Task", "Agent")
        ]
        if exit_code != 0:
            problems.append(f"exit_code={exit_code}（应 0）")
        if not started:
            problems.append("未观察到子代理调用（Task/Agent，allowed_tools_extra 未生效？）")
        if not any(e.get("type") == "agent.subagent.updated" for e in events):
            problems.append("无 agent.subagent.updated 线程事件")
        child = [
            e
            for e in events
            if e.get("parent_tool_use_id")
            and e.get("type") in ("agent.message", "tool.call.started")
        ]
        if not child:
            problems.append("无子代理线程（parent_tool_use_id）事件")
        out_path = workdir / ".node_output.json"
        if out_path.is_file():
            out = json.loads(out_path.read_text(encoding="utf-8"))
            if out.get("subagent_used") is not True:
                problems.append("submit.subagent_used 非 true")
            if not str(out.get("summary") or "").strip():
                problems.append("submit.summary 为空")
        else:
            problems.append("未产出 .node_output.json")
        return problems

    return await _run_case(
        "E-task",
        agent_spec={
            "node_key": "task-e2e",
            "node_payload": {
                "source_path": "/workspace/app",
                "task": (
                    "用 Task/Agent 子代理工具启动一个通用子代理，读取 "
                    "/workspace/app/README.md 并总结为一句中文；等子代理完成后，"
                    '调用 submit_result 提交 {"subagent_used": true, "summary": "<总结>"}。'
                ),
            },
            "submit_schema": {
                "type": "object",
                "properties": {
                    "subagent_used": {"type": "boolean"},
                    "summary": {"type": "string", "minLength": 1},
                },
                "required": ["subagent_used", "summary"],
            },
            "allowed_tools_extra": ["Task", "Agent"],
            "max_turns": 12,
        },
        skill_key=None,
        fixture=fixture,
        timeout=360,
        env=env,
        check=check,
    )


CASES = {
    "A": case_a,
    "B": case_b,
    "C": case_c,
    "D": case_d,
    "E": case_e,
}


async def main() -> None:
    wanted = [a.upper() for a in sys.argv[1:]] or list(CASES)
    unknown = [w for w in wanted if w not in CASES]
    if unknown:
        raise SystemExit(f"未知用例: {unknown}；可用: {list(CASES)}")

    env = await _resolve_env()
    print(f"[ENV] model={env.get('ANTHROPIC_MODEL')} base_url={env.get('ANTHROPIC_BASE_URL')}")
    if not agent_runner_manager.image_exists():
        raise SystemExit("[FAIL] crucible-agent-runner:base 镜像不存在（先构建）")

    results: dict[str, bool] = {}
    for key in wanted:
        print(f"\n=== Case {key} ===")
        results[key] = await CASES[key](env)

    print("\n=== 汇总 ===")
    for key, ok in results.items():
        print(f"  Case {key}: {'PASS' if ok else 'FAIL'}")
    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
