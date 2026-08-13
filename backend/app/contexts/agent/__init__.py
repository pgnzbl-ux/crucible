"""
Agent Context — Agent 执行平台(6 节点编排)。

职责:
- orchestrator.py 驱动 6 节点循环(source/profile/env_ready/audit/reproduce/report)
- AI 节点用独立 agent-runner 容器执行(context 物理隔离),通过 submit_result 工具
  回传结构化 output;节点间用 NodeRun.output_json 交接 + 断点续跑
- 收集 SDK 事件流 → 持久化 AgentEvent → Redis Pub/Sub → SSE 推前端步骤条

结构(主线):
- orchestrator.py     ★ 6 节点编排器(循环 + 分支出口 + 断点续跑)
- nodes/              ★ 6 节点实现(source/profile/env_ready/audit/reproduce/report)
- ai_runner.py        AI 节点容器编排 + submit_result 工具 + schema 校验
- profile_detector.py 节点 1 规则引擎(7 语言 + web 门禁,纯代码)
- sdk_adapter.py      Claude Agent SDK 适配器(构造注入 env)
- tasks.py            Celery 工作流(host clone → 调 orchestrator → 实时落库)

遗留(executor/runner_bridge 已被 orchestrator + ai_runner 取代,保留仅作历史参考):
- executor.py         旧 ClaudeSdkExecutor/MockExecutor + 工厂(deprecated)
- runner_bridge.py    旧容器编排组合(deprecated)
"""
