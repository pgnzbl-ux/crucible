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
      ],
      false,
    )
    expect(html).toContain('扫描·泄露')
    expect(html).toContain('启动 gitleaks')
    expect(html).toContain('二审进度')
    expect(html).toContain('已审 10')
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
