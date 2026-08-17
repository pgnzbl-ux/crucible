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

/**
 * 转换结果按输入对象缓存。事件流每来一帧都会重跑一次合并，
 * 若每次都产出新对象，下游 memo 全部失效、整屏重渲染；
 * 缺 timestamp 的帧还会每次拿到新的 created_at，排序位置跟着抖。
 */
const mappedCache = new WeakMap<StreamEvent, AgentEvent | null>()

export function sseToAgentEvent(ev: StreamEvent): AgentEvent | null {
  const cached = mappedCache.get(ev)
  if (cached !== undefined) return cached
  const mapped = ev.type === 'ready' || ev.type === 'error' ? null : buildAgentEvent(ev)
  mappedCache.set(ev, mapped)
  return mapped
}

function buildAgentEvent(ev: StreamEvent): AgentEvent {
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

/** 同一对 (REST, SSE) 事件只合成一次，保持下游 memo 命中。 */
const overlayCache = new WeakMap<AgentEvent, { sse: AgentEvent; merged: AgentEvent }>()

function overlay(rest: AgentEvent, sse: AgentEvent): AgentEvent {
  const cached = overlayCache.get(rest)
  if (cached && cached.sse === sse) return cached.merged
  const merged: AgentEvent = {
    ...rest,
    event_type: sse.event_type || rest.event_type,
    payload: Object.keys(sse.payload).length ? sse.payload : rest.payload,
  }
  overlayCache.set(rest, { sse, merged })
  return merged
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
    map.set(key, existing ? overlay(existing, mapped) : mapped)
  }
  // created_at 是 ISO 串，字典序即时间序；localeCompare 走 Intl 排序，在千条量级上明显更贵
  return [...map.values()].sort((a, b) => {
    if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1
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
