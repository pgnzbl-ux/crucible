const API_BASE = '/api/v1'

export function buildTaskEventStreamUrl(opts: {
  origin: string
  taskId: string
  token?: string | null
  lastEventId?: number | null
}): string {
  const url = new URL(`${API_BASE}/tasks/${opts.taskId}/events/stream`, opts.origin)
  if (opts.token) url.searchParams.set('token', opts.token)
  if (opts.lastEventId != null && opts.lastEventId > 0) {
    url.searchParams.set('last_event_id', String(opts.lastEventId))
  }
  return url.toString()
}
