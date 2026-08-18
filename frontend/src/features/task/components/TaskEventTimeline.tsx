import { memo, useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { Alert, Badge, Button, Collapse, Empty, Segmented, Space, Tag, Typography } from 'antd'
import {
  ArrowDownOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExperimentOutlined,
  MessageOutlined,
  NodeIndexOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'

import type { AgentEvent } from '../../../shared/lib/api'
import { EVENT_PHASE_LABELS, EVENT_TYPE_LABELS, NODE_LABELS, NODE_STATUS_META } from '../../../shared/lib/meta'
import { summarizeNodeOutput } from '../../../shared/lib/nodeOutput'
import { humanizeAgentError } from '../../../shared/lib/humanizeAgentError'
import type { SSEStatus } from '../../../shared/hooks/useTaskEvents'
import { useStickToBottom } from '../../../shared/hooks/useStickToBottom'

const { Text, Paragraph } = Typography

type StreamFilter = 'all' | 'thinking' | 'message' | 'tool' | 'error'

function payloadOf(ev: AgentEvent): Record<string, unknown> {
  const p = (ev.payload ?? {}) as Record<string, unknown>
  const nested = p.event
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    return nested as Record<string, unknown>
  }
  return p
}

function eventTime(ev: AgentEvent): string {
  const p = payloadOf(ev)
  const ts = p.timestamp
  if (typeof ts === 'number' && Number.isFinite(ts)) {
    const ms = ts > 1e12 ? ts : ts * 1000
    return dayjs(ms).format('HH:mm:ss')
  }
  return ev.created_at ? dayjs(ev.created_at).format('HH:mm:ss') : ''
}

function asText(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function truncate(text: string, max = 600): string {
  if (text.length <= max) return text
  return `${text.slice(0, max)}…`
}

function streamFooterHint(events: AgentEvent[] | undefined): string {
  const last = events?.[events.length - 1]
  const t = last?.event_type ?? ''
  if (t === 'agent.thinking' || t === 'agent.message' || t === 'tool.call.started') {
    return 'Agent 正在输出…'
  }
  if (t === 'agent.completed' || t === 'phase.updated' || t === 'node.updated') {
    return '节点执行中…'
  }
  return '任务进行中…'
}

function matchesFilter(ev: AgentEvent, filter: StreamFilter): boolean {
  if (filter === 'all') return true
  const t = ev.event_type
  if (filter === 'thinking') return t === 'agent.thinking'
  if (filter === 'message') return t === 'agent.message' || t === 'agent.completed' || t === 'phase.updated'
  if (filter === 'tool') return t.startsWith('tool.call')
  if (filter === 'error') {
    const p = payloadOf(ev)
    return (
      t.includes('failed') ||
      t === 'tool.call.denied' ||
      p.is_error === true ||
      (t === 'node.updated' && p.status === 'failed')
    )
  }
  return true
}

function typeColor(eventType: string): string {
  if (eventType.includes('failed') || eventType.endsWith('denied')) return 'var(--crucible-error)'
  if (eventType === 'agent.thinking') return 'var(--crucible-text-disabled)'
  if (eventType.startsWith('tool.call')) return 'var(--crucible-primary)'
  if (eventType === 'agent.completed') return 'var(--crucible-success)'
  if (eventType === 'node.updated') return 'var(--crucible-warning)'
  return 'var(--crucible-text-secondary)'
}

export function isEventDetailsDefaultOpen(
  eventType: string,
  payload: Record<string, unknown>,
): boolean {
  if (eventType === 'tool.call.denied') return true
  return eventType === 'tool.call.completed' && payload.is_error === true
}

/** 默认只挂最近这么多条到 DOM，长任务动辄上千条，全量挂载既费内存又拖慢每次更新。 */
export const STREAM_RENDER_WINDOW = 150

export function streamRenderWindow<T>(
  events: T[],
  showAll: boolean,
  size = STREAM_RENDER_WINDOW,
): { rows: T[]; hidden: number } {
  if (showAll || events.length <= size) return { rows: events, hidden: 0 }
  return { rows: events.slice(-size), hidden: events.length - size }
}

interface TaskEventTimelineProps {
  events: AgentEvent[] | undefined
  running: boolean
  sseEnabled: boolean
  sseStatus: SSEStatus
  sseError: string | null
  nodeLabel?: string | null
  onClearNode?: () => void
}

export function TaskEventTimeline({
  events,
  running,
  sseEnabled,
  sseStatus,
  sseError,
  nodeLabel,
  onClearNode,
}: TaskEventTimelineProps) {
  const [filter, setFilter] = useState<StreamFilter>('all')
  const [showAll, setShowAll] = useState(false)

  const filtered = useMemo(
    () => (events ?? []).filter((ev) => matchesFilter(ev, filter)),
    [events, filter],
  )
  const { rows, hidden } = useMemo(() => streamRenderWindow(filtered, showAll), [filtered, showAll])
  const last = filtered[filtered.length - 1]
  const streamKey = `${filter}:${filtered.length}:${last?.run_id ?? ''}:${last?.sequence ?? ''}`

  const { scrollRef, contentRef, handlers, pinned, scrollToBottom } = useStickToBottom(streamKey, {
    enabled: running,
  })

  // 用户上翻后统计错过的条数，回到底部时清零
  const [anchorCount, setAnchorCount] = useState<number | null>(null)
  useEffect(() => {
    setAnchorCount((prev) => (pinned ? null : (prev ?? filtered.length)))
  }, [pinned, filtered.length])
  const behindCount = anchorCount === null ? 0 : Math.max(0, filtered.length - anchorCount)

  return (
    <div className="crucible-stream-panel">
      <Space className="crucible-stream-toolbar" wrap>
        <Space>
          <Text strong>{nodeLabel ? `${nodeLabel} · 事件` : 'Agent 过程流'}</Text>
          {nodeLabel && (
            <Button size="small" type="link" style={{ paddingInline: 0 }} onClick={onClearNode}>
              查看全部
            </Button>
          )}
          {sseEnabled && (
            <Badge
              status={
                sseStatus === 'open'
                  ? 'success'
                  : sseStatus === 'reconnecting'
                    ? 'warning'
                    : sseStatus === 'connecting'
                      ? 'processing'
                      : 'default'
              }
              text={
                sseStatus === 'open'
                  ? '实时'
                  : sseStatus === 'reconnecting'
                    ? '重连中...'
                    : sseStatus === 'connecting'
                      ? '连接中'
                      : sseStatus === 'closed'
                        ? '已断开'
                        : '离线'
              }
            />
          )}
          <Text type="secondary" style={{ fontSize: 12 }}>
            {filtered.length}/{events?.length ?? 0} 条
          </Text>
        </Space>
        <Segmented
          size="small"
          value={filter}
          onChange={(v) => setFilter(v as StreamFilter)}
          options={[
            { label: '全部', value: 'all' },
            { label: '思考', value: 'thinking' },
            { label: '回复', value: 'message' },
            { label: '工具', value: 'tool' },
            { label: '错误', value: 'error' },
          ]}
        />
      </Space>
      {sseError && sseStatus === 'reconnecting' && (
        <Alert type="warning" showIcon title={sseError} style={{ marginBottom: 12 }} />
      )}
      {filtered.length > 0 ? (
        <div className="crucible-stream">
          <div
            className="crucible-stream-scroller"
            ref={scrollRef}
            tabIndex={0}
            role="log"
            aria-label={nodeLabel ? `${nodeLabel} 事件流` : 'Agent 过程流'}
            {...handlers}
          >
            <div ref={contentRef}>
              {hidden > 0 && (
                <div className="crucible-stream-earlier">
                  <Button size="small" type="link" onClick={() => setShowAll(true)}>
                    展开更早的 {hidden} 条
                  </Button>
                </div>
              )}
              {rows.map((ev) => (
                <StreamRow key={`${ev.run_id}-${ev.sequence}-${ev.event_type}`} ev={ev} />
              ))}
              {running && (
                <div className="crucible-stream-footer">
                  <span className="crucible-stream-pulse" />
                  {streamFooterHint(events)}
                </div>
              )}
            </div>
          </div>
          {running && !pinned && (
            <Button
              className="crucible-stream-jump"
              size="small"
              type="primary"
              shape="round"
              icon={<ArrowDownOutlined />}
              onClick={scrollToBottom}
            >
              {behindCount > 0 ? `${behindCount} 条新事件` : '回到最新'}
            </Button>
          )}
        </div>
      ) : (
        <Empty
          description={nodeLabel ? `「${nodeLabel}」暂无事件` : '暂无执行事件'}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      )}
    </div>
  )
}

/** 行内容只由事件本身决定，memo 让新事件到达时只挂新行，不重渲染既有行。 */
const StreamRow = memo(function StreamRow({ ev }: { ev: AgentEvent }) {
  const p = payloadOf(ev)
  const label = EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type
  const color = typeColor(ev.event_type)

  return (
    <div className="crucible-stream-row" style={{ '--stream-accent': color } as CSSProperties}>
      <Text type="secondary" className="crucible-stream-time">
        {eventTime(ev)}
      </Text>
      <Tag className="crucible-stream-tag" variant="filled">
        {label}
      </Tag>
      <div className="crucible-stream-body">{renderBody(ev, p)}</div>
    </div>
  )
})

function renderBody(ev: AgentEvent, p: Record<string, unknown>) {
  if (ev.event_type === 'agent.thinking') {
    const text = asText(p.text)
    return (
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: 't',
            label: (
              <Text type="secondary" italic style={{ fontSize: 12 }}>
                {truncate(text.replace(/\s+/g, ' '), 80) || '思考中'}
              </Text>
            ),
            children: (
              <Paragraph
                style={{
                  marginBottom: 0,
                  whiteSpace: 'pre-wrap',
                  fontStyle: 'italic',
                  color: 'var(--crucible-text-secondary)',
                  fontSize: 12,
                }}
              >
                {text}
              </Paragraph>
            ),
          },
        ]}
      />
    )
  }

  if (ev.event_type === 'agent.message') {
    return (
      <Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 13 }}>
        <MessageOutlined style={{ marginRight: 6, color: 'var(--crucible-primary)' }} />
        {asText(p.text)}
      </Paragraph>
    )
  }

  if (ev.event_type === 'tool.call.started') {
    const input = asText(p.input)
    return (
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: 'details',
            label: (
              <Text>
                <ToolOutlined style={{ marginRight: 6 }} />
                调用 <Text code>{asText(p.tool) || 'unknown'}</Text>
              </Text>
            ),
            children: input ? (
              <Paragraph
                type="secondary"
                style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 11 }}
              >
                {truncate(input, 800)}
              </Paragraph>
            ) : (
              <Text type="secondary" style={{ fontSize: 11 }}>无输入参数</Text>
            ),
          },
        ]}
      />
    )
  }

  if (ev.event_type === 'tool.call.completed') {
    const isError = p.is_error === true
    const output = asText(p.output)
    return (
      <Collapse
        ghost
        size="small"
        defaultActiveKey={isEventDetailsDefaultOpen(ev.event_type, p) ? ['details'] : []}
        items={[
          {
            key: 'details',
            label: (
              <Text type={isError ? 'danger' : undefined}>
                <ToolOutlined style={{ marginRight: 6 }} />
                {isError ? '工具返回错误' : '工具完成'}
              </Text>
            ),
            children: output ? (
              <Paragraph
                type={isError ? 'danger' : 'secondary'}
                style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 11 }}
              >
                {truncate(output, 1200)}
              </Paragraph>
            ) : (
              <Text type="secondary" style={{ fontSize: 11 }}>无输出内容</Text>
            ),
          },
        ]}
      />
    )
  }

  if (ev.event_type === 'tool.call.denied') {
    const reason = asText(p.reason || p.error) || '该工具调用不符合安全策略'
    const input = asText(p.input)
    return (
      <Collapse
        ghost
        size="small"
        defaultActiveKey={isEventDetailsDefaultOpen(ev.event_type, p) ? ['details'] : []}
        items={[
          {
            key: 'details',
            label: (
              <Text type="danger">
                <ToolOutlined style={{ marginRight: 6 }} />
                已拒绝 <Text code>{asText(p.tool) || 'unknown'}</Text>
              </Text>
            ),
            children: (
              <div>
                <Text type="danger">{reason}</Text>
                {input && (
                  <Paragraph
                    type="secondary"
                    style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', fontSize: 11 }}
                  >
                    {truncate(input, 800)}
                  </Paragraph>
                )}
              </div>
            ),
          },
        ]}
      />
    )
  }

  if (ev.event_type === 'agent.failed') {
    const raw = asText(p.error || p.text)
    const title = asText(p.title) || humanizeAgentError(raw).title
    const hint = asText(p.hint) || humanizeAgentError(raw).hint
    return (
      <Alert
        type="error"
        showIcon
        icon={<CloseCircleOutlined />}
        title={title}
        description={
          <div>
            {raw && raw !== title && (
              <Paragraph style={{ marginBottom: 8, whiteSpace: 'pre-wrap', fontSize: 12 }}>
                原因: {raw}
              </Paragraph>
            )}
            <Text>下一步: {hint}</Text>
            {p.traceback ? (
              <Paragraph
                type="secondary"
                style={{ margin: '8px 0 0', whiteSpace: 'pre-wrap', fontSize: 11 }}
              >
                {truncate(asText(p.traceback), 1500)}
              </Paragraph>
            ) : null}
          </div>
        }
      />
    )
  }

  if (ev.event_type === 'node.updated') {
    const key = asText(p.node_key)
    const status = asText(p.status)
    const meta = NODE_STATUS_META[status]
    const output = (p.output && typeof p.output === 'object' ? p.output : {}) as Record<string, unknown>
    const summary = summarizeNodeOutput(key, output, status)
    return (
      <Space wrap>
        <NodeIndexOutlined />
        <Text>{NODE_LABELS[key] ?? key}</Text>
        <Tag color={meta?.color}>{meta?.label ?? status}</Tag>
        {summary && summary !== (meta?.label ?? status) && (
          <Text type={status === 'failed' ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
            {truncate(summary, 240)}
          </Text>
        )}
      </Space>
    )
  }

  if (ev.event_type === 'phase.updated') {
    const phase = asText(p.phase)
    return (
      <Text>
        {EVENT_PHASE_LABELS[phase] ?? phase} {asText(p.message) ? `· ${asText(p.message)}` : ''}
      </Text>
    )
  }

  if (ev.event_type === 'agent.completed') {
    return (
      <Text>
        <CheckCircleOutlined style={{ color: 'var(--crucible-success)', marginRight: 6 }} />
        Agent 完成
        {p.conclusion ? `，结论: ${asText(p.conclusion)}` : ''}
        {p.is_error === true ? <Text type="danger">（带错误）</Text> : null}
      </Text>
    )
  }

  if (ev.event_type === 'raw.message') {
    return (
      <Text type="secondary" style={{ fontSize: 12 }}>
        <ExperimentOutlined style={{ marginRight: 6 }} />
        {asText(p.message_type)} {truncate(asText(p.raw), 400)}
      </Text>
    )
  }

  const fallback = asText(p.text || p.message || p.error)
  return <Text>{fallback || ev.event_type}</Text>
}
