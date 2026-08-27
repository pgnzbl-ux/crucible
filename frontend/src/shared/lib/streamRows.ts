/** 事件流分组层：把扁平 SSE 事件折叠成 Cursor 式的行描述。
 *
 * 纯函数、无 React 依赖；TaskEventTimeline 按 StreamRow 渲染：
 * - 连续 agent.thinking 折叠为一条思考组
 * - tool.call.started/completed 按 tool_use_id 配对成一条可折叠行
 * - 其余类型原样透传为单事件行
 */
import type { AgentEvent } from './api'

function payloadOf(ev: AgentEvent): Record<string, unknown> {
  const p = (ev.payload ?? {}) as Record<string, unknown>
  const nested = p.event
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    return nested as Record<string, unknown>
  }
  return p
}

function threadOf(ev: AgentEvent): string {
  const v = payloadOf(ev).parent_tool_use_id
  return typeof v === 'string' && v ? v : '__main__'
}

function evKey(ev: AgentEvent): string {
  return `${ev.run_id}-${ev.sequence}-${ev.event_type}`
}

export type StreamRow =
  | { kind: 'event'; ev: AgentEvent; key: string }
  /** 连续思考段折叠组 */
  | { kind: 'thinking'; evs: AgentEvent[]; key: string }
  /** 命令执行与结果合并行；done 为 null 表示仍在执行 */
  | { kind: 'tool'; start: AgentEvent | null; done: AgentEvent | null; key: string }

/**
 * 单遍扫描：thinking 连续段归并；started 先占位、completed 到达时回填同一行，
 * 流式增量下"运行中→有结果"切换不产生行的重排。
 */
export function buildStreamRows(events: readonly AgentEvent[]): StreamRow[] {
  const out: StreamRow[] = []
  const openTools = new Map<string, StreamRow & { kind: 'tool' }>()
  let thinkingBuf: AgentEvent[] = []

  const flushThinking = () => {
    if (thinkingBuf.length > 0) {
      out.push({ kind: 'thinking', evs: thinkingBuf, key: evKey(thinkingBuf[0]) })
      thinkingBuf = []
    }
  }

  for (const ev of events) {
    if (ev.event_type === 'agent.thinking') {
      thinkingBuf.push(ev)
      continue
    }
    flushThinking()

    if (ev.event_type === 'tool.call.started') {
      const id = String(payloadOf(ev).tool_use_id ?? '')
      const row: StreamRow & { kind: 'tool' } = {
        kind: 'tool',
        start: ev,
        done: null,
        key: evKey(ev),
      }
      if (id) openTools.set(id, row)
      out.push(row)
      continue
    }
    if (ev.event_type === 'tool.call.completed') {
      const id = String(payloadOf(ev).tool_use_id ?? '')
      const open = id ? openTools.get(id) : undefined
      if (open && open.done === null) {
        open.done = ev
        continue
      }
      // 孤儿结果（无 started 或已配对过）：保留原样，避免静默丢日志
      out.push({ kind: 'tool', start: null, done: ev, key: evKey(ev) })
      continue
    }
    out.push({ kind: 'event', ev, key: evKey(ev) })
  }
  flushThinking()
  return out
}

// ------------------------------------------------------------------
// 主 / 子代理线程
// ------------------------------------------------------------------

export const MAIN_THREAD = '__main__'

export interface ThreadInfo {
  /** ToolUseBlock 的 id，即子代理线程键 */
  id: string
  /** 展示名：优先生命周期 description，其次 Task 调用 description/prompt 摘要 */
  label: string
  status: string
  /** 该线程下的归属事件数（不含其派生的孙线程） */
  count: number
}

function shortText(v: unknown, max = 40): string {
  if (typeof v !== 'string') return ''
  return v.length > max ? `${v.slice(0, max)}…` : v
}

/** 子代理派发工具名：新版 CLI 叫 Agent，Task 是旧别名。 */
export const SUBAGENT_TOOLS = new Set(['Task', 'Agent'])

/** 从流内推导子代理列表：Task/Agent 调用建立条目，agent.subagent.updated 更新状态。 */
export function deriveThreads(events: readonly AgentEvent[]): ThreadInfo[] {
  const map = new Map<string, ThreadInfo>()
  for (const ev of events) {
    const p = payloadOf(ev)
    if (ev.event_type === 'tool.call.started' && SUBAGENT_TOOLS.has(String(p.tool ?? ''))) {
      const t = threadOf(ev)
      const id = String(p.tool_use_id ?? '')
      if (!id || map.has(id)) continue
      map.set(id, {
        id,
        label: shortText(p.description) || shortText((p.input as Record<string, unknown>)?.description)
          || shortText((p.input as Record<string, unknown>)?.prompt, 24),
        status: 'running',
        count: 0,
      })
      continue
    }
    if (ev.event_type === 'agent.subagent.updated') {
      const id = typeof p.tool_use_id === 'string' ? p.tool_use_id : ''
      if (!id) continue
      const info = map.get(id) ?? { id, label: '', status: '', count: 0 }
      const label = typeof p.label === 'string' ? p.label.trim() : ''
      if (label) info.label = label
      const st = typeof p.status === 'string' ? p.status : ''
      if (st) info.status = st
      map.set(id, info)
      continue
    }
    const t = threadOf(ev)
    if (t !== MAIN_THREAD) {
      const info = map.get(t)
      if (info) info.count += 1
    }
  }
  return [...map.values()]
}

/** 线程过滤：仅保留选中线程的归属事件（lifecycle 广播始终放行到主线程）。 */
export function filterByThread(
  events: readonly AgentEvent[],
  thread: string,
): AgentEvent[] {
  return events.filter((ev) => {
    // 子代理生命周期广播本身在主线程上：切到具体子代理时也要看得到它
    if (ev.event_type === 'agent.subagent.updated') return true
    const t = threadOf(ev)
    if (t === MAIN_THREAD) return thread === MAIN_THREAD
    return thread === MAIN_THREAD ? false : t === thread
  })
}
