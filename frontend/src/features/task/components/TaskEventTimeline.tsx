import { Alert, Badge, Empty, Space, Timeline, Typography } from 'antd'
import dayjs from 'dayjs'

import type { AgentEvent } from '../../../shared/lib/api'
import { EVENT_PHASE_LABELS } from '../../../shared/lib/meta'
import type { SSEStatus } from '../../../shared/hooks/useTaskEvents'

const { Text } = Typography

function eventMessage(ev: AgentEvent): string {
  const p = ev.payload
  const msg = p.message as string | undefined
  if (msg) return msg
  if (ev.event_type === 'tool.call.completed') {
    return `调用工具: ${String(p.tool ?? 'unknown')}`
  }
  if (ev.event_type === 'agent.completed') {
    return `Agent 完成，结论: ${String(p.conclusion ?? '')}`
  }
  return ev.event_type
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
  const timelineItems =
    events?.map((ev) => {
      const p = ev.payload as Record<string, unknown>
      // phase 可能在顶层(REST AgentEvent.payload)或嵌套在 event 字段(SSE 帧)
      const nested = p.event as Record<string, unknown> | undefined
      const phase = (nested?.phase ?? p.phase) as string | undefined
      return {
        color: ev.event_type.includes('failed') ? 'red' : running ? 'blue' : 'green',
        children: (
          <div>
            <Text strong>{EVENT_PHASE_LABELS[phase ?? ''] ?? eventMessage(ev)}</Text>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {eventMessage(ev)}
              </Text>
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {dayjs(ev.created_at).format('HH:mm:ss')}
            </Text>
          </div>
        ),
      }
    }) ?? []

  return (
    <div>
      <Space style={{ marginBottom: 12, width: '100%', justifyContent: 'space-between' }}>
        <Text strong>Agent 执行进度</Text>
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
      </Space>
      {sseError && sseStatus === 'reconnecting' && (
        <Alert type="warning" showIcon message={sseError} style={{ marginBottom: 12 }} />
      )}
      {events && events.length > 0 ? (
        <Timeline items={timelineItems} />
      ) : (
        <Empty description="暂无执行事件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </div>
  )
}
