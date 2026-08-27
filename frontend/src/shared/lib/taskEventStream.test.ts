import { describe, expect, it } from 'vitest'

import { buildTaskEventStreamUrl } from './taskEventStream'

describe('buildTaskEventStreamUrl', () => {
  it('prefers ticket over token', () => {
    const url = buildTaskEventStreamUrl({
      origin: 'http://localhost:5173',
      taskId: 't1',
      ticket: 'sse-ticket',
      token: 'jwt',
    })
    const parsed = new URL(url)
    expect(parsed.pathname).toBe('/api/v1/tasks/t1/events/stream')
    expect(parsed.searchParams.get('ticket')).toBe('sse-ticket')
    expect(parsed.searchParams.has('token')).toBe(false)
    expect(parsed.searchParams.has('last_event_id')).toBe(false)
  })

  it('falls back to token for legacy clients', () => {
    const url = buildTaskEventStreamUrl({
      origin: 'http://localhost:5173',
      taskId: 't1',
      token: 'jwt',
    })
    expect(new URL(url).searchParams.get('token')).toBe('jwt')
  })

  it('adds last_event_id on reconnect so the server can skip replayed sequences', () => {
    const url = buildTaskEventStreamUrl({
      origin: 'http://localhost:5173',
      taskId: 't1',
      ticket: 'sse-ticket',
      lastEventId: 42,
    })
    expect(new URL(url).searchParams.get('last_event_id')).toBe('42')
  })
})
