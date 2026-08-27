const API_BASE = '/api/v1'

export function buildTaskEventStreamUrl(opts: {
  origin?: string
  taskId: string
  lastEventId?: number | null
}): string {
  const base = opts.origin || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost')
  const url = new URL(`${API_BASE}/tasks/${opts.taskId}/events/stream`, base)
  if (opts.lastEventId != null && opts.lastEventId > 0) {
    url.searchParams.set('last_event_id', String(opts.lastEventId))
  }
  return url.toString()
}

