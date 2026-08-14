import { describe, expect, it } from 'vitest'

import type { AgentEvent } from './api'
import { eventsForRun, mergeTaskEvents, dropNoisyEvents } from './taskEvents'

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

describe('mergeTaskEvents', () => {
  it('sorts by created_at then sequence, not sequence across runs', () => {
    const rest = [
      ev({ id: 'a', run_id: 'r2', sequence: 1, created_at: '2026-08-13T10:00:00Z' }),
      ev({ id: 'b', run_id: 'r1', sequence: 1, created_at: '2026-08-13T08:00:00Z' }),
    ]
    const merged = mergeTaskEvents(rest, [])
    expect(merged.map((e) => e.id)).toEqual(['b', 'a'])
  })
})
