import { App } from 'antd'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { AgentEvent } from '../../../shared/lib/api'
import {
  isEventDetailsDefaultOpen,
  streamRenderWindow,
  STREAM_RENDER_WINDOW,
  TaskEventTimeline,
} from './TaskEventTimeline'

describe('isEventDetailsDefaultOpen', () => {
  it('keeps thinking and successful tool details collapsed', () => {
    expect(isEventDetailsDefaultOpen('agent.thinking', {})).toBe(false)
    expect(isEventDetailsDefaultOpen('tool.call.started', {})).toBe(false)
    expect(isEventDetailsDefaultOpen('tool.call.completed', { is_error: false })).toBe(false)
  })

  it('keeps failed and denied tool details expanded', () => {
    expect(isEventDetailsDefaultOpen('tool.call.completed', { is_error: true })).toBe(true)
    expect(isEventDetailsDefaultOpen('tool.call.denied', {})).toBe(true)
  })
})

describe('streamRenderWindow', () => {
  const events = Array.from({ length: STREAM_RENDER_WINDOW + 40 }, (_, i) => i)

  it('renders everything while under the window size', () => {
    const short = events.slice(0, 10)
    expect(streamRenderWindow(short, false)).toEqual({ rows: short, hidden: 0 })
  })

  it('keeps only the newest events mounted and reports the rest', () => {
    const { rows, hidden } = streamRenderWindow(events, false)
    expect(rows).toHaveLength(STREAM_RENDER_WINDOW)
    expect(rows[rows.length - 1]).toBe(events[events.length - 1])
    expect(hidden).toBe(40)
  })

  it('mounts everything once the user asks for earlier events', () => {
    expect(streamRenderWindow(events, true)).toEqual({ rows: events, hidden: 0 })
  })
})

function event(sequence: number, over: Partial<AgentEvent> = {}): AgentEvent {
  return {
    id: `e${sequence}`,
    run_id: 'r1',
    sequence,
    event_type: 'agent.message',
    payload: { text: `消息 ${sequence}` },
    source: 'sse',
    created_at: '2026-08-17T06:00:00Z',
    ...over,
  }
}

function render(events: AgentEvent[], running: boolean, extra?: { nodeLabel?: string }) {
  return renderToStaticMarkup(
    <App>
      <TaskEventTimeline
        events={events}
        running={running}
        sseEnabled={running}
        sseStatus={running ? 'open' : 'idle'}
        sseError={null}
        nodeLabel={extra?.nodeLabel}
      />
    </App>,
  )
}

describe('TaskEventTimeline 事件流窗口', () => {
  it('运行中渲染可滚动窗口与实时提示', () => {
    const html = render([event(1), event(2)], true)
    expect(html).toContain('crucible-stream-scroller')
    expect(html).toContain('crucible-stream-row')
    expect(html).toContain('crucible-stream-footer')
    expect(html).toContain('消息 2')
  })

  it('默认贴底，不显示回到最新按钮', () => {
    const html = render([event(1)], true)
    expect(html).not.toContain('crucible-stream-jump')
  })

  it('任务结束后不再显示进行中脚注', () => {
    const html = render([event(1, { event_type: 'agent.completed', payload: {} })], false)
    expect(html).toContain('crucible-stream-scroller')
    expect(html).not.toContain('crucible-stream-footer')
  })

  it('无事件时给出空态', () => {
    const html = render([], true)
    expect(html).not.toContain('crucible-stream-scroller')
    expect(html).toContain('暂无执行事件')
  })

  it('超出渲染窗口时只挂最近的行并给出展开入口', () => {
    const many = Array.from({ length: STREAM_RENDER_WINDOW + 5 }, (_, i) => event(i + 1))
    const html = render(many, true)
    expect(html).toContain('展开更早的 5 条')
    expect(html).not.toContain('消息 1<')
    expect(html).toContain(`消息 ${STREAM_RENDER_WINDOW + 5}`)
  })

  it('按节点过滤时标题带节点名，空态说明该节点没有事件', () => {
    const filtered = render([event(1)], false, { nodeLabel: '白盒审计' })
    expect(filtered).toContain('白盒审计')
    expect(filtered).toContain('查看全部')
    const empty = render([], false, { nodeLabel: '白盒审计' })
    expect(empty).toContain('「白盒审计」暂无事件')
  })

  it('renders discovery scan phase and triage progress in the stream', () => {
    const html = render(
      [
        event(1, {
          event_type: 'phase.updated',
          payload: { phase: 'scan_gitleaks', message: '启动 gitleaks' },
        }),
        event(2, {
          event_type: 'triage.progress',
          payload: { adjudicated: 10, pending: 3, reason: 'budget' },
        }),
        event(3, {
          event_type: 'triage.progress',
          payload: {
            node_key: 'triage',
            message: '二审 3/12：CWE-89 app/db.py（族内 2 组）',
            done: 3,
            total: 12,
          },
        }),
      ],
      false,
    )
    expect(html).toContain('扫描·泄露')
    expect(html).toContain('启动 gitleaks')
    expect(html).toContain('二审进度')
    expect(html).toContain('已审 10')
    expect(html).toContain('二审 3/12：CWE-89 app/db.py（族内 2 组）')
  })

  it('renders profile phase labels in the stream', () => {
    const html = render(
      [
        event(1, {
          event_type: 'phase.updated',
          payload: { phase: 'profile', message: '规则扫描完成（python/fastapi · 1 语言）' },
        }),
        event(2, {
          event_type: 'phase.updated',
          payload: { phase: 'profile', message: '启动轻度 AI 画像' },
        }),
      ],
      false,
    )
    expect(html).toContain('项目画像')
    expect(html).toContain('规则扫描完成')
    expect(html).toContain('启动轻度 AI 画像')
  })
})

describe('TaskEventTimeline 分组与线程（Cursor 式）', () => {
  const nested = (sequence: number, type: string, p: Record<string, unknown>): AgentEvent => ({
    id: `n${sequence}`,
    run_id: 'r1',
    sequence,
    event_type: type,
    payload: { event: { ...p, timestamp: 1756250400 + sequence } },
    source: 'claude-agent-sdk',
    created_at: '2026-08-27T00:00:00Z',
  })

  it('命令执行与结果合并为一条可折叠行，成功默认收起', () => {
    const html = render([
      nested(1, 'tool.call.started', {
        tool: 'Bash',
        input: { command: 'pytest -q tests/' },
        tool_use_id: 't9',
      }),
      nested(2, 'tool.call.completed', { tool_use_id: 't9', output: '24 passed', is_error: false }),
    ], false)
    expect(html).toContain('pytest -q')
    expect(html).toContain('已完成')
    // 合并后不应再有独立的"工具结束"行
    expect(html).not.toContain('>工具结束<')
  })

  it('连续思考折叠为思考过程组并显示段数', () => {
    const html = render([
      nested(1, 'agent.thinking', { text: '先看入口' }),
      nested(2, 'agent.thinking', { text: '再看汇聚点' }),
    ], false)
    expect(html).toContain('思考过程 · 2 段')
    expect(html).toContain('先看入口')
  })

  it('Task 调用渲染主/子代理切换芯片，生命周期事件展示子代理状态', () => {
    const html = render([
      nested(1, 'tool.call.started', {
        tool: 'Task',
        input: { description: '族 A 二审' },
        tool_use_id: 'tu_1',
      }),
      nested(2, 'agent.subagent.updated', {
        tool_use_id: 'tu_1',
        label: '族 A 二审',
        status: 'completed',
      }),
      nested(3, 'tool.call.started', {
        tool: 'Bash',
        tool_use_id: 'b1',
        input: { command: 'ls' },
        parent_tool_use_id: 'tu_1',
      }),
    ], true)
    expect(html).toContain('主 Agent')
    expect(html).toContain('族 A 二审')
    expect(html).toContain('data-thread="main"')
    expect(html).toContain('data-thread="tu_1"')
    expect(html).toContain('子代理 · 族 A 二审')
  })
})
