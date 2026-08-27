import { describe, expect, it } from 'vitest'

import type { AgentEvent } from './api'
import { MAIN_THREAD, buildStreamRows, deriveThreads, filterByThread } from './streamRows'

let seq = 0
function ev(event_type: string, payload: Record<string, unknown>): AgentEvent {
  seq += 1
  // 与后端 REST/SSE 一致：payload 内嵌套一层 {event:{…}}
  return {
    id: `e${seq}`,
    run_id: 'r1',
    sequence: seq,
    event_type,
    payload: { event: payload },
    source: 'claude-agent-sdk',
    created_at: '2026-08-27T00:00:00Z',
  }
}

describe('buildStreamRows', () => {
  it('连续思考折叠为一组，跨类型边界重新开组', () => {
    const rows = buildStreamRows([
      ev('agent.thinking', { text: 'a' }),
      ev('agent.thinking', { text: 'b' }),
      ev('agent.message', { text: 'hi' }),
      ev('agent.thinking', { text: 'c' }),
    ])
    expect(rows.map((r) => r.kind)).toEqual(['thinking', 'event', 'thinking'])
    expect((rows[0] as { kind: 'thinking'; evs: AgentEvent[] }).evs).toHaveLength(2)
  })

  it('started+completed 按 tool_use_id 合并成一行并回填结果', () => {
    const rows = buildStreamRows([
      ev('tool.call.started', {
        tool: 'Bash',
        input: { command: 'pytest -q' },
        tool_use_id: 't1',
      }),
      ev('tool.call.completed', { tool_use_id: 't1', output: 'ok', is_error: false }),
    ])
    expect(rows).toHaveLength(1)
    const tool = rows[0] as { kind: 'tool'; start: AgentEvent; done: AgentEvent }
    const dp = tool.done.payload.event as Record<string, unknown>
    const sp = tool.start.payload.event as Record<string, unknown>
    expect(dp.output).toBe('ok')
    expect((sp.input as Record<string, unknown>).command).toBe('pytest -q')
  })

  it('无结果的 started 保持 pending（done=null），孤儿 completed 单独成行', () => {
    const rows = buildStreamRows([
      ev('tool.call.started', { tool: 'Bash', tool_use_id: 'p1' }),
      ev('tool.call.completed', { tool_use_id: 'ghost', output: '?' }),
    ])
    expect(rows.map((r) => r.kind)).toEqual(['tool', 'tool'])
    expect((rows[0] as { done: null }).done).toBeNull()
    expect((rows[1] as { start: null }).start).toBeNull()
  })

  it('其余类型原样透传为 event 行且顺序稳定', () => {
    const e1 = ev('node.updated', { node_key: 'triage', status: 'completed' })
    const e2 = ev('phase.updated', { phase: 'triage', message: 'x' })
    const rows = buildStreamRows([e1, e2])
    expect(rows.map((r) => r.kind)).toEqual(['event', 'event'])
  })
})

describe('deriveThreads / filterByThread', () => {
  const taskStart = ev('tool.call.started', {
    tool: 'Task',
    input: { description: '族 A 二审', prompt: '详细审查 SQL 注入' },
    tool_use_id: 'tu_1',
  })
  const inner = ev('tool.call.started', {
    tool: 'Bash',
    tool_use_id: 'b9',
    parent_tool_use_id: 'tu_1',
  })
  const lifecycle = ev('agent.subagent.updated', {
    tool_use_id: 'tu_1',
    label: '族 A 二审',
    status: 'completed',
  })
  const taskDone = ev('tool.call.completed', {
    tool_use_id: 'tu_1',
    output: '确认存在 SQL 注入漏洞 CWE-89',
    is_error: false,
  })
  const otherLifecycle = ev('agent.subagent.updated', {
    tool_use_id: 'tu_other',
    label: '无关子代理',
    status: 'completed',
  })
  const mainMsg = ev('agent.message', { text: 'hello' })

  it('Task 调用建立子代理条目；tool.call.completed 更新产出与状态', () => {
    const threads = deriveThreads([taskStart, inner, lifecycle, taskDone, mainMsg])
    expect(threads).toHaveLength(1)
    const th = threads[0]
    expect(th.id).toBe('tu_1')
    expect(th.label).toBe('族 A 二审')
    expect(th.status).toBe('completed')
    expect(th.output).toBe('确认存在 SQL 注入漏洞 CWE-89')
    expect(th.count).toBe(2) // inner + taskDone
  })

  it('主线程视图隐藏子代理归属事件但保留主流程', () => {
    const main = filterByThread([taskStart, inner, lifecycle, taskDone, mainMsg], MAIN_THREAD)
    const ids = new Set(main)
    expect(ids.has(inner)).toBe(false)
    expect(ids.has(taskStart)).toBe(true)
    expect(ids.has(taskDone)).toBe(true)
    expect(ids.has(mainMsg)).toBe(true)
  })

  it('子代理视图包含发起、内部执行、专属广播和最终完成结果，排除其他子代理泄漏与主线程消息', () => {
    const sub = filterByThread([taskStart, inner, lifecycle, otherLifecycle, taskDone, mainMsg], 'tu_1')
    expect(sub.includes(taskStart)).toBe(true)
    expect(sub.includes(inner)).toBe(true)
    expect(sub.includes(lifecycle)).toBe(true)
    expect(sub.includes(taskDone)).toBe(true)
    expect(sub.includes(otherLifecycle)).toBe(false) // 避免跨子代理泄漏
    expect(sub.includes(mainMsg)).toBe(false)
  })
})

describe('SUBAGENT_TOOLS / Agent 新名 与 buildStreamRows 子代理行', () => {
  it('Agent 工具（新版 CLI）同样建立子代理线程', () => {
    const agentStart = ev('tool.call.started', {
      tool: 'Agent',
      input: { description: '族 B 二审' },
      tool_use_id: 'ag_1',
    })
    const inner = ev('agent.message', { text: 'x', parent_tool_use_id: 'ag_1' })
    const threads = deriveThreads([agentStart, inner])
    expect(threads).toHaveLength(1)
    expect(threads[0]).toMatchObject({ id: 'ag_1', label: '族 B 二审', count: 1 })
  })

  it('Task/Agent 工具启动和完成合并为 subagent 行', () => {
    const start = ev('tool.call.started', {
      tool: 'Task',
      input: { description: '代码审计子任务' },
      tool_use_id: 'sub_99',
    })
    const done = ev('tool.call.completed', {
      tool_use_id: 'sub_99',
      output: '发现 2 处潜在高危漏洞',
      is_error: false,
    })
    const rows = buildStreamRows([start, done])
    expect(rows).toHaveLength(1)
    expect(rows[0].kind).toBe('subagent')
    const subRow = rows[0] as { kind: 'subagent'; start: AgentEvent; done: AgentEvent }
    expect(subRow.start).toBe(start)
    expect(subRow.done).toBe(done)
  })
})
