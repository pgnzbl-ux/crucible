"""
Agent Context — Agent 执行平台（能力 catalog + discovery/verify 两子图）。

职责:
- orchestrator.py 按 pipeline_for(task_type) 驱动 discovery 全图或 verify 子图
- AI 节点用独立 agent-runner 容器执行(context 物理隔离),通过 submit_result 工具
  回传结构化 output;节点间用 NodeRun.output_json 交接 + 断点续跑
- discovery 终认由 lead_verify → LeadWorker 逐线索执行 audit/reproduce，落 LeadNodeRun
- finalize 固化 analysis_verdict / analysis_status（任务权威终态）；report 为后处理文档
- 收集 SDK 事件流 → 持久化 AgentEvent → Redis Pub/Sub → SSE 推前端步骤条

结构(主线):
- contracts/          ★ NodeSpec / Input / Handoff / ControlSignals + DEFAULT/VERIFY_PIPELINE
- orchestrator.py     ★ 有限 DAG 编排器(就绪波 + 分支出口 + 断点续跑 + finalize 封口)
- nodes/              ★ 节点实现(含 scan_*/api_*/lead_verify/finalize/report；verify 另有 audit/reproduce)
- lead_worker.py      discovery 逐线索终认
- ai_runner.py        AI 节点容器编排 + submit_result 工具 + schema 校验
- profile_detector.py 节点 1 规则引擎(7 语言 + web 门禁,纯代码)
- sdk_adapter.py      Claude Agent SDK 适配器(构造注入 env)
- tasks.py            Celery 工作流(host clone → 调 orchestrator → 实时落库)
"""
