import { describe, expect, it } from 'vitest'

import { buildTaskEventStreamUrl } from './taskEventStream'

describe('buildTaskEventStreamUrl', () => {
  it('omits last_event_id on first connect', () => {
    const url = buildTaskEventStreamUrl({
      origin: 'http://localhost:5173',
      taskId: 't1',
      token: 'jwt',
    })
    const parsed = new URL(url)
    expect(parsed.pathname).toBe('/api/v1/tasks/t1/events/stream')
    expect(parsed.searchParams.get('token')).toBe('jwt')
    expect(parsed.searchParams.has('last_event_id')).toBe(false)
  })

  it('adds last_event_id on reconnect so the server can skip replayed sequences', () => {
    const url = buildTaskEventStreamUrl({
      origin: 'http://localhost:5173',
      taskId: 't1',
      token: 'jwt',
      lastEventId: 42,
    })
    expect(new URL(url).searchParams.get('last_event_id')).toBe('42')
  })
})
