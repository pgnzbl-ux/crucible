import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Badge, Collapse, Empty, Segmented, Space, Tag, Typography } from 'antd'
import {
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
import { humanizeAgentError } from '../../../shared/lib/humanizeAgentError'
import type { SSEStatus } from '../../../shared/hooks/useTaskEvents'

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

interface TaskEventTimelineProps {
  events: AgentEvent[] | undefined
  running: boolean
  sseEnabled: boolean
  sseStatus: SSEStatus
  sseError: string | null
}

export function TaskEventTimeline({
  events,
  running,
  sseEnabled,
  sseStatus,
  sseError,
}: TaskEventTimelineProps) {
  const [filter, setFilter] = useState<StreamFilter>('all')
  const scrollerRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(
    () => (events ?? []).filter((ev) => matchesFilter(ev, filter)),
    [events, filter],
  )

  useEffect(() => {
    if (!running) return
    const el = scrollerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [filtered.length, running])

  return (
    <div>
      <Space style={{ marginBottom: 12, width: '100%', justifyContent: 'space-between' }} wrap>
        <Space>
          <Text strong>Agent 过程流</Text>
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
        <Alert type="warning" showIcon message={sseError} style={{ marginBottom: 12 }} />
      )}
      {filtered.length > 0 ? (
        <div
          ref={scrollerRef}
          style={{
            maxHeight: 560,
            overflow: 'auto',
            background: 'var(--crucible-bg)',
            border: '1px solid var(--crucible-border)',
            borderRadius: 8,
            padding: '8px 0',
            fontFamily: 'var(--crucible-font-mono)',
          }}
        >
          {filtered.map((ev) => (
            <StreamRow key={`${ev.run_id}-${ev.sequence}-${ev.event_type}`} ev={ev} />
          ))}
          {running && (
            <div style={{ padding: '8px 16px', color: 'var(--crucible-text-secondary)', fontSize: 12 }}>
              Agent 正在输出…
            </div>
          )}
        </div>
      ) : (
        <Empty description="暂无执行事件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </div>
  )
}

function StreamRow({ ev }: { ev: AgentEvent }) {
  const p = payloadOf(ev)
  const label = EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type
  const color = typeColor(ev.event_type)

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '64px 72px 1fr',
        gap: 8,
        padding: '6px 16px',
        borderLeft: `3px solid ${color}`,
        alignItems: 'start',
      }}
    >
      <Text type="secondary" style={{ fontSize: 11, lineHeight: '22px' }}>
        {eventTime(ev)}
      </Text>
      <Tag style={{ marginInlineEnd: 0, fontSize: 11 }} bordered={false}>
        {label}
      </Tag>
      <div style={{ minWidth: 0 }}>{renderBody(ev, p)}</div>
    </div>
  )
}

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
    return (
      <div>
        <Text>
          <ToolOutlined style={{ marginRight: 6 }} />
          调用 <Text code>{asText(p.tool) || 'unknown'}</Text>
        </Text>
        {p.input != null && p.input !== '' && (
          <Paragraph
            type="secondary"
            style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', fontSize: 11 }}
          >
            {truncate(asText(p.input), 800)}
          </Paragraph>
        )}
      </div>
    )
  }

  if (ev.event_type === 'tool.call.completed') {
    const isError = p.is_error === true
    return (
      <div>
        <Text type={isError ? 'danger' : undefined}>
          <ToolOutlined style={{ marginRight: 6 }} />
          {isError ? '工具返回错误' : '工具完成'}
        </Text>
        {p.output != null && p.output !== '' && (
          <Paragraph
            type={isError ? 'danger' : 'secondary'}
            style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', fontSize: 11 }}
          >
            {truncate(asText(p.output), 1200)}
          </Paragraph>
        )}
      </div>
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
        message={title}
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
    return (
      <Space>
        <NodeIndexOutlined />
        <Text>{NODE_LABELS[key] ?? key}</Text>
        <Tag color={meta?.color}>{meta?.label ?? status}</Tag>
        {status === 'failed' && (
          <Text type="danger">{asText(p.title || p.error || (p.output as { error?: string } | undefined)?.error)}</Text>
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
