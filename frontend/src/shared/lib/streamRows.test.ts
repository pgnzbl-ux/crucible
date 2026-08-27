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
    expect(sp.input.command).toBe('pytest -q')
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
    input: { description: '族 A 二审' },
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
  const mainMsg = ev('agent.message', { text: 'hello' })

  it('Task 调用建立子代理条目；生命周期更新状态；计数只含归属事件', () => {
    const threads = deriveThreads([taskStart, inner, lifecycle, mainMsg])
    expect(threads).toHaveLength(1)
    const th = threads[0]
    expect(th.id).toBe('tu_1')
    expect(th.label).toBe('族 A 二审')
    expect(th.status).toBe('completed')
    expect(th.count).toBe(1)
  })

  it('主线程视图隐藏子代理归属事件但保留生命周期广播', () => {
    const main = filterByThread([taskStart, inner, lifecycle, mainMsg], MAIN_THREAD)
    // 主线程隐藏子代理归属事件（按 id 断言，类型可能同名）
    const ids = new Set(main)
    expect(ids.has(inner)).toBe(false)
    expect(ids.has(lifecycle)).toBe(true)
    expect(ids.has(taskStart)).toBe(true)
    expect(ids.has(mainMsg)).toBe(true)
  })

  it('子代理视图只显示该线程事件与广播，不显示主线程消息', () => {
    const sub = filterByThread([taskStart, inner, lifecycle, mainMsg], 'tu_1')
    expect(sub.includes(inner)).toBe(true)
    expect(sub.includes(lifecycle)).toBe(true)
    expect(sub.includes(mainMsg)).toBe(false)
  })
})
