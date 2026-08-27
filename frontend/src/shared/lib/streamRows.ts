/** 事件流分组层：把扁平 SSE 事件折叠成 Cursor / Codex 式的行描述。
 *
 * 纯函数、无 React 依赖；TaskEventTimeline 按 StreamRow 渲染：
 * - 连续 agent.thinking 折叠为一条思考组
 * - tool.call.started/completed 按 tool_use_id 配对成一条可折叠行
 * - Task / Agent 子代理派发与结果识别为 subagent 复合行
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

/** 子代理派发工具名：新版 CLI 叫 Agent，Task 是旧别名。 */
export const SUBAGENT_TOOLS = new Set(['Task', 'Agent'])

export type StreamRow =
  | { kind: 'event'; ev: AgentEvent; key: string }
  /** 连续思考段折叠组 */
  | { kind: 'thinking'; evs: AgentEvent[]; key: string }
  /** 命令执行与结果合并行；done 为 null 表示仍在执行 */
  | { kind: 'tool'; start: AgentEvent | null; done: AgentEvent | null; key: string }
  /** 子代理行：启动信息与最终完成结论合并，若在独立线程中也可直接展示结论 */
  | {
      kind: 'subagent'
      id: string
      tool: string
      start: AgentEvent | null
      done: AgentEvent | null
      key: string
    }

/**
 * 单遍扫描：thinking 连续段归并；started 先占位、completed 到达时回填同一行，
 * 流式增量下"运行中→有结果"切换不产生行的重排。
 */
export function buildStreamRows(events: readonly AgentEvent[]): StreamRow[] {
  const out: StreamRow[] = []
  const openTools = new Map<string, StreamRow & { kind: 'tool' }>()
  const openSubagents = new Map<string, StreamRow & { kind: 'subagent' }>()
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

    // 1. Tool Call Started
    if (ev.event_type === 'tool.call.started') {
      const p = payloadOf(ev)
      const id = String(p.tool_use_id ?? '')
      const tool = String(p.tool ?? '')

      if (SUBAGENT_TOOLS.has(tool)) {
        const subRow: StreamRow & { kind: 'subagent' } = {
          kind: 'subagent',
          id,
          tool,
          start: ev,
          done: null,
          key: evKey(ev),
        }
        if (id) openSubagents.set(id, subRow)
        out.push(subRow)
        continue
      }

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

    // 2. Tool Call Completed
    if (ev.event_type === 'tool.call.completed') {
      const p = payloadOf(ev)
      const id = String(p.tool_use_id ?? '')

      // 优先匹配 subagent
      const openSub = id ? openSubagents.get(id) : undefined
      if (openSub && openSub.done === null) {
        openSub.done = ev
        continue
      }

      // 匹配普通 tool
      const open = id ? openTools.get(id) : undefined
      if (open && open.done === null) {
        open.done = ev
        continue
      }

      // 孤儿结果（无 started 或已配对过）：判断是否为子代理结果
      const toolName = String(p.tool ?? '')
      if (SUBAGENT_TOOLS.has(toolName) || (id && openSubagents.has(id))) {
        out.push({
          kind: 'subagent',
          id,
          tool: toolName || 'Subagent',
          start: null,
          done: ev,
          key: evKey(ev),
        })
        continue
      }

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
  /** 任务完整 Prompt 或 Description */
  description?: string
  status: string
  /** 最终输出/结论（由 tool.call.completed 获得） */
  output?: string
  /** 是否出错 */
  isError?: boolean
  /** 该线程下的归属事件数（不含其派生的孙线程） */
  count: number
}

function shortText(v: unknown, max = 40): string {
  if (typeof v !== 'string') return ''
  return v.length > max ? `${v.slice(0, max)}…` : v
}

/** 从流内推导子代理列表：Task/Agent 调用建立条目，agent.subagent.updated 及 tool.call.completed 更新状态与产出。 */
export function deriveThreads(events: readonly AgentEvent[]): ThreadInfo[] {
  const map = new Map<string, ThreadInfo>()
  for (const ev of events) {
    const p = payloadOf(ev)
    // 1. 发起子代理
    if (ev.event_type === 'tool.call.started' && SUBAGENT_TOOLS.has(String(p.tool ?? ''))) {
      const id = String(p.tool_use_id ?? '')
      if (!id) continue
      const inputObj = (p.input && typeof p.input === 'object' ? p.input : {}) as Record<string, unknown>
      const descShort =
        shortText(p.description) ||
        shortText(inputObj.description) ||
        shortText(inputObj.prompt, 24)
      const fullDesc = String(p.description || inputObj.description || inputObj.prompt || '')
      const existing = map.get(id) ?? { id, label: '', status: 'running', count: 0 }
      if (!existing.label && descShort) existing.label = descShort
      if (!existing.description && fullDesc) existing.description = fullDesc
      if (!existing.status) existing.status = 'running'
      map.set(id, existing)
      continue
    }

    // 2. 状态广播
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

    // 3. 完成返回（包含最终 output 结果）
    if (ev.event_type === 'tool.call.completed') {
      const id = String(p.tool_use_id ?? '')
      if (id && map.has(id)) {
        const info = map.get(id)!
        const rawOutput = p.output
        info.output = typeof rawOutput === 'string' ? rawOutput : rawOutput ? JSON.stringify(rawOutput, null, 2) : ''
        info.isError = p.is_error === true
        if (!info.status || info.status === 'running') {
          info.status = p.is_error === true ? 'failed' : 'completed'
        }
        info.count += 1
        continue
      }
    }

    // 4. 内部归属事件计数
    const t = threadOf(ev)
    if (t !== MAIN_THREAD) {
      const info = map.get(t)
      if (info) info.count += 1
    }
  }
  return [...map.values()]
}

/**
 * 线程过滤：
 * - 主线程：保留主线程事件与生命周期广播，隐藏子代理内部事件。
 * - 子代理线程：包含该子代理的发起调用、内部执行事件、专属广播、以及最终完成返回结果。
 */
export function filterByThread(
  events: readonly AgentEvent[],
  thread: string,
): AgentEvent[] {
  if (thread === MAIN_THREAD) {
    return events.filter((ev) => {
      const t = threadOf(ev)
      return t === MAIN_THREAD
    })
  }

  return events.filter((ev) => {
    const p = payloadOf(ev)
    const tuId = String(p.tool_use_id ?? '')
    // 该子代理自身的发起调用与完成结果
    if (
      (ev.event_type === 'tool.call.started' || ev.event_type === 'tool.call.completed') &&
      tuId === thread
    ) {
      return true
    }
    // 仅匹配该子代理自身的生命周期广播（避免多代理事件互相泄漏）
    if (ev.event_type === 'agent.subagent.updated' && tuId === thread) {
      return true
    }
    // 该子代理内部产生的事件
    const t = threadOf(ev)
    return t === thread
  })
}

