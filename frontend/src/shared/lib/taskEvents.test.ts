import { describe, expect, it } from 'vitest'

import type { AgentEvent } from './api'
import { eventsForRun, eventsForNode, mergeTaskEvents, dropNoisyEvents, sseToAgentEvent } from './taskEvents'

function ev(partial: Partial<AgentEvent> & Pick<AgentEvent, 'id' | 'run_id' | 'sequence'>): AgentEvent {
  return {
    event_type: 'phase.updated',
    payload: { phase: 'preflight', message: '创建工作区' },
    source: 'crucible',
    created_at: '2026-08-13T01:00:00Z',
    ...partial,
  }
}

describe('eventsForRun', () => {
  it('keeps only the latest run so retries do not stack preflight lines', () => {
    const events = [
      ev({ id: '1', run_id: 'old', sequence: 1, created_at: '2026-08-13T08:00:00Z' }),
      ev({ id: '2', run_id: 'new', sequence: 1, created_at: '2026-08-13T10:00:00Z' }),
    ]
    const kept = eventsForRun(events, 'new')
    expect(kept).toHaveLength(1)
    expect(kept[0].run_id).toBe('new')
  })

  it('hides thinking_tokens phase spam from the timeline', () => {
    const events = [
      ev({
        id: '1',
        run_id: 'new',
        sequence: 1,
        event_type: 'phase.updated',
        payload: { phase: 'start', message: 'thinking_tokens' },
      }),
      ev({
        id: '2',
        run_id: 'new',
        sequence: 2,
        event_type: 'agent.message',
        payload: { text: 'hello' },
      }),
    ]
    const kept = dropNoisyEvents(events)
    expect(kept).toHaveLength(1)
    expect(kept[0].id).toBe('2')
  })
})

describe('eventsForNode', () => {
  it('returns the full stream when no node is selected', () => {
    const events = [
      ev({ id: '1', run_id: 'r', sequence: 1 }),
      ev({ id: '2', run_id: 'r', sequence: 2, event_type: 'agent.message', payload: { text: 'hi' } }),
    ]
    expect(eventsForNode(events, null)).toEqual(events)
    expect(eventsForNode(events, undefined)).toEqual(events)
  })

  it('keeps thinking and tool events that follow a node.updated running marker', () => {
    const events = [
      ev({
        id: 'pre',
        run_id: 'r',
        sequence: 1,
        event_type: 'phase.updated',
        payload: { phase: 'preflight', message: '工作区' },
      }),
      ev({
        id: 'audit-run',
        run_id: 'r',
        sequence: 2,
        event_type: 'node.updated',
        payload: { node_key: 'audit', status: 'running' },
      }),
      ev({
        id: 'think',
        run_id: 'r',
        sequence: 3,
        event_type: 'agent.thinking',
        payload: { text: '推演利用链' },
      }),
      ev({
        id: 'tool',
        run_id: 'r',
        sequence: 4,
        event_type: 'tool.call.started',
        payload: { tool: 'Read' },
      }),
      ev({
        id: 'audit-done',
        run_id: 'r',
        sequence: 5,
        event_type: 'node.updated',
        payload: { node_key: 'audit', status: 'completed' },
      }),
      ev({
        id: 'repro-run',
        run_id: 'r',
        sequence: 6,
        event_type: 'node.updated',
        payload: { node_key: 'reproduce', status: 'running' },
      }),
      ev({
        id: 'curl',
        run_id: 'r',
        sequence: 7,
        event_type: 'tool.call.started',
        payload: { tool: 'Bash' },
      }),
    ]
    expect(eventsForNode(events, 'audit').map((e) => e.id)).toEqual([
      'audit-run',
      'think',
      'tool',
      'audit-done',
    ])
    expect(eventsForNode(events, 'reproduce').map((e) => e.id)).toEqual(['repro-run', 'curl'])
    expect(eventsForNode(events, 'source')).toEqual([])
  })

  it('attributes phase.updated whose phase is a node key even without a prior marker', () => {
    const events = [
      ev({
        id: 'env',
        run_id: 'r',
        sequence: 1,
        event_type: 'phase.updated',
        payload: { phase: 'env_ready', message: 'Building web' },
      }),
      ev({
        id: 'msg',
        run_id: 'r',
        sequence: 2,
        event_type: 'agent.message',
        payload: { text: 'compose up' },
      }),
    ]
    expect(eventsForNode(events, 'env_ready').map((e) => e.id)).toEqual(['env', 'msg'])
  })

  it('attributes live thinking by sequence even if created_at is earlier than the running marker', () => {
    const events = [
      ev({
        id: 'profile-done',
        run_id: 'r',
        sequence: 2,
        created_at: '2026-08-18T03:00:02Z',
        event_type: 'node.updated',
        payload: { node_key: 'profile', status: 'completed' },
      }),
      ev({
        id: 'env-run',
        run_id: 'r',
        sequence: 3,
        created_at: '2026-08-18T03:00:04Z',
        event_type: 'node.updated',
        payload: { node_key: 'env_ready', status: 'running' },
      }),
      ev({
        id: 'think',
        run_id: 'r',
        sequence: 4,
        created_at: '2026-08-18T03:00:01Z',
        event_type: 'agent.thinking',
        payload: { text: '写 Dockerfile' },
      }),
    ]
    expect(eventsForNode(events, 'env_ready').map((e) => e.id)).toEqual(['env-run', 'think'])
    expect(eventsForNode(events, 'profile').map((e) => e.id)).toEqual(['profile-done'])
  })
})

describe('mergeTaskEvents', () => {
  it('sorts different runs by created_at, not sequence', () => {
    const rest = [
      ev({ id: 'a', run_id: 'r2', sequence: 1, created_at: '2026-08-13T10:00:00Z' }),
      ev({ id: 'b', run_id: 'r1', sequence: 1, created_at: '2026-08-13T08:00:00Z' }),
    ]
    const merged = mergeTaskEvents(rest, [])
    expect(merged.map((e) => e.id)).toEqual(['b', 'a'])
  })

  it('keeps same-run order by sequence even if created_at is inverted', () => {
    const rest = [
      ev({
        id: 'think',
        run_id: 'r1',
        sequence: 4,
        created_at: '2026-08-18T03:00:01Z',
        event_type: 'agent.thinking',
        payload: { text: '写 Dockerfile' },
      }),
      ev({
        id: 'env-run',
        run_id: 'r1',
        sequence: 3,
        created_at: '2026-08-18T03:00:04Z',
        event_type: 'node.updated',
        payload: { node_key: 'env_ready', status: 'running' },
      }),
    ]
    const merged = mergeTaskEvents(rest, [])
    expect(merged.map((e) => e.id)).toEqual(['env-run', 'think'])
  })

  // 事件流每来一帧都重跑合并，对象身份必须稳定，否则下游 memo 全部失效
  it('reuses converted objects for the same SSE frame', () => {
    const frame = { type: 'agent.message', run_id: 'r1', sequence: 1, event: { text: 'hi' } }
    expect(sseToAgentEvent(frame)).toBe(sseToAgentEvent(frame))
  })

  it('keeps identity of untouched events across repeated merges', () => {
    const rest = [ev({ id: 'a', run_id: 'r1', sequence: 1 })]
    const frames = [{ type: 'agent.message', run_id: 'r1', sequence: 2, event: { text: 'hi' } }]
    const first = mergeTaskEvents(rest, frames)
    const second = mergeTaskEvents(rest, frames)
    expect(second[0]).toBe(first[0])
    expect(second[1]).toBe(first[1])
  })

  it('keeps identity when an SSE frame overlays a REST event', () => {
    const rest = [ev({ id: 'a', run_id: 'r1', sequence: 1, payload: {} })]
    const frames = [{ type: 'agent.message', run_id: 'r1', sequence: 1, event: { text: 'hi' } }]
    const first = mergeTaskEvents(rest, frames)
    const second = mergeTaskEvents(rest, frames)
    expect(first).toHaveLength(1)
    expect(first[0].payload).toEqual({ text: 'hi' })
    expect(second[0]).toBe(first[0])
  })

  it('gives frames without timestamp a stable created_at', () => {
    const frame = { type: 'agent.message', run_id: 'r1', sequence: 9, event: { text: 'hi' } }
    const first = sseToAgentEvent(frame)?.created_at
    expect(sseToAgentEvent(frame)?.created_at).toBe(first)
  })
})
