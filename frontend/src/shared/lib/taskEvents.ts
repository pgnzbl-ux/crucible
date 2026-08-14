import type { AgentEvent } from './api'

export interface StreamEvent {
  type: string
  sequence?: number
  run_id?: string
  event?: unknown
}

function unixToIso(ts: unknown): string | null {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return null
  const ms = ts > 1e12 ? ts : ts * 1000
  return new Date(ms).toISOString()
}

export function sseToAgentEvent(ev: StreamEvent): AgentEvent | null {
  if (ev.type === 'ready' || ev.type === 'error') return null
  const inner = (ev.event && typeof ev.event === 'object' ? ev.event : {}) as Record<string, unknown>
  return {
    id: `sse-${ev.run_id ?? ''}-${ev.sequence ?? 'x'}`,
    run_id: ev.run_id ?? '',
    sequence: ev.sequence ?? 0,
    event_type: ev.type,
    payload: inner,
    source: 'sse',
    created_at: unixToIso(inner.timestamp) ?? new Date().toISOString(),
  }
}

export function mergeTaskEvents(rest: AgentEvent[] | undefined, sse: StreamEvent[]): AgentEvent[] {
  const map = new Map<string, AgentEvent>()
  for (const ev of rest ?? []) {
    map.set(`${ev.run_id}:${ev.sequence}`, ev)
  }
  for (const raw of sse) {
    const mapped = sseToAgentEvent(raw)
    if (!mapped) continue
    const key = `${mapped.run_id}:${mapped.sequence}`
    const existing = map.get(key)
    if (existing) {
      map.set(key, {
        ...existing,
        event_type: mapped.event_type || existing.event_type,
        payload: Object.keys(mapped.payload).length ? mapped.payload : existing.payload,
      })
    } else {
      map.set(key, mapped)
    }
  }
  return [...map.values()].sort((a, b) => {
    const byTime = a.created_at.localeCompare(b.created_at)
    if (byTime !== 0) return byTime
    return a.sequence - b.sequence
  })
}

/** 丢掉 SDK thinking_tokens 用量心跳，避免事件流被刷屏。 */
export function dropNoisyEvents(events: AgentEvent[]): AgentEvent[] {
  return events.filter((ev) => {
    if (ev.event_type !== 'phase.updated') return true
    const p = (ev.payload ?? {}) as Record<string, unknown>
    const nested = p.event
    const body =
      nested && typeof nested === 'object' && !Array.isArray(nested)
        ? (nested as Record<string, unknown>)
        : p
    return body.message !== 'thinking_tokens'
  })
}

export function eventsForRun(events: AgentEvent[], runId: string | undefined): AgentEvent[] {
  if (!runId) return events
  return events.filter((e) => e.run_id === runId)
}
