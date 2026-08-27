const API_BASE = '/api/v1'

export function buildTaskEventStreamUrl(opts: {
  origin: string
  taskId: string
  /** 短命 SSE ticket（推荐） */
  ticket?: string | null
  /** @deprecated 仅开发兼容；生产后端拒绝 */
  token?: string | null
  lastEventId?: number | null
}): string {
  const url = new URL(`${API_BASE}/tasks/${opts.taskId}/events/stream`, opts.origin)
  if (opts.ticket) {
    url.searchParams.set('ticket', opts.ticket)
  } else if (opts.token) {
    url.searchParams.set('token', opts.token)
  }
  if (opts.lastEventId != null && opts.lastEventId > 0) {
    url.searchParams.set('last_event_id', String(opts.lastEventId))
  }
  return url.toString()
}
