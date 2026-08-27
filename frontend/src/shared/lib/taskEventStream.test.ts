import { describe, expect, it } from 'vitest'

import { buildTaskEventStreamUrl } from './taskEventStream'

describe('buildTaskEventStreamUrl', () => {
  it('builds clean SSE stream url without credentials in query', () => {
    const url = buildTaskEventStreamUrl({
      origin: 'http://localhost:5173',
      taskId: 't1',
    })
    const parsed = new URL(url)
    expect(parsed.pathname).toBe('/api/v1/tasks/t1/events/stream')
    expect(parsed.searchParams.has('ticket')).toBe(false)
    expect(parsed.searchParams.has('token')).toBe(false)
    expect(parsed.searchParams.has('last_event_id')).toBe(false)
  })

  it('adds last_event_id on reconnect so the server can skip replayed sequences', () => {
    const url = buildTaskEventStreamUrl({
      origin: 'http://localhost:5173',
      taskId: 't1',
      lastEventId: 42,
    })
    expect(new URL(url).searchParams.get('last_event_id')).toBe('42')
  })
})

