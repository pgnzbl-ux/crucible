import { memo, useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import {
  Alert,
  Badge,
  Button,
  Collapse,
  Empty,
  Segmented,
  Space,
  Tag,
  Typography,
} from 'antd'
import {
  ArrowDownOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  EditOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  LoadingOutlined,
  MessageOutlined,
  NodeIndexOutlined,
  SendOutlined,
  TeamOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'

import type { AgentEvent } from '../../../shared/lib/api'
import { EVENT_PHASE_LABELS, EVENT_TYPE_LABELS, NODE_LABELS, NODE_STATUS_META } from '../../../shared/lib/meta'
import {
  MAIN_THREAD,
  buildStreamRows,
  deriveThreads,
  filterByThread,
  type StreamRow as RowModel,
} from '../../../shared/lib/streamRows'
import { summarizeNodeOutput } from '../../../shared/lib/nodeOutput'
import { humanizeAgentError } from '../../../shared/lib/humanizeAgentError'
import type { SSEStatus } from '../../../shared/hooks/useTaskEvents'
import { useErrorToast } from '../../../shared/hooks/useErrorToast'
import { useStickToBottom } from '../../../shared/hooks/useStickToBottom'

const { Text, Paragraph } = Typography
const { CheckableTag } = Tag

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
  if (filter === 'message') {
    return t === 'agent.message' || t === 'agent.completed' || t === 'phase.updated' || t === 'triage.progress'
  }
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
  if (eventType === 'agent.subagent.updated') return 'var(--crucible-primary)'
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

// ---- 工具行图标：按工具名选择，使命令类执行一眼可辨 ----

function toolIcon(tool: string) {
  if (!tool) return <ToolOutlined />
  if (tool === 'Bash' || tool === 'PowerShell') return <CodeOutlined />
  if (tool === 'Read' || tool === 'Grep' || tool === 'Glob') return <FileSearchOutlined />
  if (tool === 'Edit' || tool === 'Write' || tool === 'NotebookEdit') return <EditOutlined />
  if (tool === 'Task') return <TeamOutlined />
  if (tool.startsWith('mcp__crucible__submit')) return <SendOutlined />
  return <ToolOutlined />
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
  /** 当前查看的线程；MAIN_THREAD = 主 Agent */
  const [thread, setThread] = useState<string>(MAIN_THREAD)
  useErrorToast(sseStatus === 'reconnecting' && !!sseError, sseError, '实时连接中断，正在重连')

  // 切换节点时回到主线程，避免残留的子代理选中态找不到归属
  useEffect(() => {
    setThread(MAIN_THREAD)
    setShowAll(false)
  }, [nodeLabel])

  const threads = useMemo(() => deriveThreads(events ?? []), [events])

  // 过滤链：类型过滤 → 线程过滤 → 分组折叠 → 尾部窗口
  const scoped = useMemo(() => {
    const byType = (events ?? []).filter((ev) => matchesFilter(ev, filter))
    return filterByThread(byType, thread)
  }, [events, filter, thread])

  const grouped = useMemo(() => buildStreamRows(scoped), [scoped])
  const { rows, hidden } = useMemo(
    () => streamRenderWindow(grouped, showAll),
    [grouped, showAll],
  )

  const last = scoped[scoped.length - 1]
  const streamKey = `${filter}:${thread}:${scoped.length}:${last?.run_id ?? ''}:${last?.sequence ?? ''}`

  const { scrollRef, contentRef, handlers, pinned, scrollToBottom } = useStickToBottom(streamKey, {
    enabled: running,
  })

  // 用户上翻后统计错过的条数，回到底部时清零
  const [anchorCount, setAnchorCount] = useState<number | null>(null)
  useEffect(() => {
    setAnchorCount((prev) => (pinned ? null : (prev ?? scoped.length)))
  }, [pinned, scoped.length])
  const behindCount = anchorCount === null ? 0 : Math.max(0, scoped.length - anchorCount)

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
            {scoped.length}/{events?.length ?? 0} 条
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
      {threads.length > 0 && (
        <div className="crucible-stream-threads">
          <CheckableTag
            className="crucible-thread-chip"
            checked={thread === MAIN_THREAD}
            onChange={() => setThread(MAIN_THREAD)}
            data-thread="main"
          >
            <MessageOutlined style={{ marginRight: 4 }} />
            主 Agent
          </CheckableTag>
          {threads.map((th) => (
            <CheckableTag
              key={th.id}
              className="crucible-thread-chip"
              checked={thread === th.id}
              onChange={() => setThread(thread === th.id ? MAIN_THREAD : th.id)}
              data-thread={th.id}
            >
              <TeamOutlined style={{ marginRight: 4 }} />
              {th.label || th.id}
              <Badge
                status={
                  running && !th.status ? 'processing' : th.status === 'completed' ? 'success' : 'default'
                }
                style={{ marginLeft: 6 }}
              />
            </CheckableTag>
          ))}
        </div>
      )}
      {rows.length > 0 ? (
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
              {rows.map((row) => (
                <StreamRowComponent key={row.key} row={row} />
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
const StreamRowComponent = memo(function StreamRow({ row }: { row: RowModel }) {
  const head =
    row.kind === 'event'
      ? row.ev
      : row.kind === 'thinking'
        ? row.evs[0]
        : (row.start ?? row.done!)
  const p = payloadOf(head)
  const color =
    row.kind === 'tool'
      ? typeColor(
          row.done && payloadOf(row.done).is_error === true ? 'tool.error' : 'tool',
        )
      : typeColor(head.event_type)

  let body
  let tagLabel
  if (row.kind === 'thinking') {
    tagLabel = EVENT_TYPE_LABELS['agent.thinking'] ?? 'thinking'
    body = <ThinkingGroupBody evs={row.evs} />
  } else if (row.kind === 'tool') {
    tagLabel = EVENT_TYPE_LABELS['tool.call.started'] ?? 'tool'
    body = <MergedToolBody start={row.start} done={row.done} />
  } else {
    tagLabel = EVENT_TYPE_LABELS[head.event_type] ?? head.event_type
    body = renderBody(head, p)
  }

  return (
    <div className="crucible-stream-row" style={{ '--stream-accent': color } as CSSProperties}>
      <Text type="secondary" className="crucible-stream-time">
        {eventTime(head)}
      </Text>
      <Tag className="crucible-stream-tag" variant="filled">
        {tagLabel}
      </Tag>
      <div className="crucible-stream-body">{body}</div>
    </div>
  )
})

/** 连续思考折叠组：默认收起，展开后逐段完整展示。 */
function ThinkingGroupBody({ evs }: { evs: AgentEvent[] }) {
  const texts = evs.map((ev) => asText(payloadOf(ev).text)).filter(Boolean)
  const preview = truncate((texts[0] || '').replace(/\s+/g, ' '), 80)
  return (
    <Collapse
      ghost
      size="small"
      items={[
        {
          key: 'thinking',
          label: (
            <Text type="secondary" italic style={{ fontSize: 12 }}>
              思考过程 · {texts.length} 段{preview ? ` · ${preview}` : ''}
            </Text>
          ),
          children: (
            <div>
              {texts.map((text, i) => (
                <Paragraph
                  key={i}
                  style={{
                    marginBottom: i === texts.length - 1 ? 0 : 8,
                    whiteSpace: 'pre-wrap',
                    fontStyle: 'italic',
                    color: 'var(--crucible-text-secondary)',
                    fontSize: 12,
                  }}
                >
                  {text}
                </Paragraph>
              ))}
            </div>
          ),
        },
      ]}
    />
  )
}

/**
 * 工具合并行：命令（输入）与结果共用一条，默认收起、出错自动展开；
 * 尚无结果时显示运行中态。
 */
function MergedToolBody({
  start,
  done,
}: {
  start: AgentEvent | null
  done: AgentEvent | null
}) {
  const sp = start ? payloadOf(start) : {}
  const dp = done ? payloadOf(done) : {}
  const tool = (asText(sp.tool) || asText(dp.tool)) ?? ''
  const inputObj = (
    sp.input && typeof sp.input === 'object'
      ? (sp.input as Record<string, unknown>)
      : {}
  )
  const command =
    asText(dp.command) || asText(sp.command) || asText(inputObj.command)
  const inputJson = asText(sp.input)
  const output = asText(dp.output)
  const isError = dp.is_error === true
  const pending = done === null

  if (!start && done) {
    // 孤儿结果：按旧行为渲染完成信息，避免静默丢日志
    return (
      <Collapse
        ghost
        size="small"
        defaultActiveKey={isError ? ['details'] : []}
        items={[
          {
            key: 'details',
            label: (
              <Text type={isError ? 'danger' : undefined}>
                {toolIcon(asText(dp.tool))}
                <span style={{ marginLeft: 6 }}>
                  {isError ? '工具返回错误' : '工具完成'}
                  {asText(dp.tool) ? <Text code style={{ marginLeft: 6 }}>{asText(dp.tool)}</Text> : null}
                </span>
              </Text>
            ),
            children: output ? (
              <Paragraph type={isError ? 'danger' : 'secondary'} style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 11 }}>
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

  const denyInfo = asText(sp.reason || sp.error)

  return (
    <Collapse
      ghost
      size="small"
      defaultActiveKey={
        pending || isError || denyInfo ? ['details'] : []
      }
      items={[
        {
          key: 'details',
          label: (
            <Text type={isError ? 'danger' : undefined}>
              {toolIcon(tool)}
              <span style={{ marginLeft: 6 }}>
                {pending ? '调用中' : isError ? '返回错误' : denyInfo ? '已拒绝' : '已完成'}
                <Text code style={{ marginLeft: 6 }}>{tool || 'unknown'}</Text>
              </span>
              {command && (
                <Text type="secondary" code style={{ marginLeft: 10, fontSize: 11 }}>
                  {truncate(command.replace(/\s+/g, ' '), 60)}
                </Text>
              )}
              {pending && (
                <LoadingOutlined spin style={{ marginLeft: 8, color: 'var(--crucible-primary)' }} />
              )}
            </Text>
          ),
          children: (
            <div>
              {denyInfo && <Text type="danger">{denyInfo}</Text>}
              {inputJson && inputJson !== '{}' && (
                <Paragraph
                  type="secondary"
                  style={{ marginBottom: output ? 8 : 0, whiteSpace: 'pre-wrap', fontSize: 11 }}
                >
                  {truncate(inputJson, 800)}
                </Paragraph>
              )}
              {output ? (
                <Paragraph
                  type={isError ? 'danger' : 'secondary'}
                  style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 11 }}
                >
                  {truncate(output, 1200)}
                </Paragraph>
              ) : pending ? (
                <Text type="secondary" style={{ fontSize: 11 }}>等待结果…</Text>
              ) : (
                <Text type="secondary" style={{ fontSize: 11 }}>无输出内容</Text>
              )}
            </div>
          ),
        },
      ]}
    />
  )
}

function renderBody(ev: AgentEvent, p: Record<string, unknown>) {
  if (ev.event_type === 'agent.subagent.updated') {
    const label = asText(p.label) || asText(p.tool_use_id) || '子代理'
    const status = asText(p.status)
    return (
      <Text type="secondary" style={{ fontSize: 12 }}>
        <TeamOutlined style={{ marginRight: 6, color: 'var(--crucible-primary)' }} />
        子代理 · {label}
        {status ? ` · ${status}` : ''}
      </Text>
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
    const phaseLabel = EVENT_PHASE_LABELS[phase] ?? NODE_LABELS[phase] ?? phase
    return (
      <Text>
        {phaseLabel} {asText(p.message) ? `· ${asText(p.message)}` : ''}
      </Text>
    )
  }

  if (ev.event_type === 'triage.progress') {
    const message = asText(p.message)
    if (message) {
      return <Text>{message}{asText(p.reason) === 'budget' && !message.includes('预算') ? '（预算中断）' : ''}</Text>
    }
    const done = asText(p.adjudicated)
    const pending = asText(p.pending)
    const reason = asText(p.reason)
    return (
      <Text>
        已审 {done}
        {pending ? `，待审 ${pending}` : ''}
        {reason === 'budget' ? '（预算中断）' : ''}
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
