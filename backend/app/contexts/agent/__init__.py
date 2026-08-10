"""
Agent Context — Agent 执行平台。

职责：
- 将漏洞分析任务翻译为 Agent 执行（Claude Agent SDK / Mock）
- 编排 agent-runner 容器（与代码层物理隔离，凭据零落盘）
- 收集 SDK 事件流（stdout JSONL）→ 持久化 AgentEvent → Redis Pub/Sub 发布

结构：
- sdk_adapter.py    Claude Agent SDK 适配器（构造注入 env + prompt）
- runner_bridge.py  容器编排 + 写 prompt + 流消费组合
- executor.py       执行器抽象 + 实现（ClaudeSdkExecutor / MockExecutor）+ 工厂
- tasks.py          Celery 工作流编排
"""