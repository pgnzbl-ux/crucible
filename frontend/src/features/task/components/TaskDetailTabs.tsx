import { useEffect, useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Divider,
  Skeleton,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import type { AgentEvent, TaskDetail } from '../../../shared/lib/api'
import { api } from '../../../shared/lib/api'
import { getStatusMeta, getPriorityMeta, getVerdictMeta } from '../../../shared/lib/meta'
import { useTaskEvents, type SSEEvent } from '../../../shared/hooks/useTaskEvents'
import { NodeSteps } from '../../../shared/components/NodeSteps'
import { ReportContent } from '../../../shared/components/ReportContent'
import { EvidenceList } from './EvidenceList'
import { TaskEventTimeline } from './TaskEventTimeline'

const { Title, Paragraph, Text } = Typography

interface TaskDetailTabsProps {
  taskId: string
}

export function TaskDetailTabs({ taskId }: TaskDetailTabsProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState('overview')

  const { data: task, isLoading: taskLoading } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId),
  })

  const running = task ? ['queued', 'running'].includes(task.status) : false
  const sseEnabled = !!taskId && running
  const { events: sseEvents, status: sseStatus, error: sseError } = useTaskEvents(taskId, {
    enabled: sseEnabled,
  })

  useEffect(() => {
    const last = sseEvents[sseEvents.length - 1]
    if (!last) return
    if (last.type === 'agent.completed' || last.type === 'agent.failed' || last.type === 'node.updated') {
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['task-report', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
    }
  }, [sseEvents, qc, taskId])

  const events: AgentEvent[] | undefined = sseEvents.length
    ? sseEvents.map((ev) => ({
        id: `${ev.sequence ?? 'x'}`,
        run_id: ev.run_id ?? '',
        sequence: ev.sequence ?? 0,
        event_type: ev.type,
        payload: (ev.event ?? {}) as Record<string, unknown>,
        source: 'sse',
        created_at: new Date().toISOString(),
      }))
    : undefined

  const { data: report } = useQuery({
    queryKey: ['task-report', taskId],
    queryFn: () => api.getReportByTask(taskId),
    enabled: !!taskId,
    retry: false,
  })

  const publishMutation = useMutation({
    mutationFn: (rid: string) => api.publishReport(rid),
    onSuccess: () => message.success('报告已发布'),
    onError: (e: Error) => message.error(e.message),
  })

  if (taskLoading && !task) {
    return <Skeleton active paragraph={{ rows: 8 }} />
  }

  if (!task) {
    return <Alert type="error" message="任务不存在或无权访问" />
  }

  const st = getStatusMeta(task.status)

  const tabItems = [
    {
      key: 'overview',
      label: '概览',
      children: <OverviewTab task={task} statusColor={st.color ?? 'default'} statusLabel={st.label} />,
    },
    {
      key: 'nodes',
      label: '节点进度',
      children: (
        <NodeSteps
          taskId={task.id}
          runId={task.runs[0]?.id}
          sseEvents={sseEvents as unknown as SSEEvent[]}
        />
      ),
    },
    {
      key: 'events',
      label: '事件流',
      children: (
        <TaskEventTimeline
          events={events}
          running={running}
          sseEnabled={sseEnabled}
          sseStatus={sseStatus}
          sseError={sseError}
        />
      ),
    },
    {
      key: 'report',
      label: '报告',
      children: report ? (
        <Card bordered={false}>
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="状态">{report.status}</Descriptions.Item>
            <Descriptions.Item label="判定">
              {report.verdict ? (
                <Tag color={getVerdictMeta(report.verdict).color}>{getVerdictMeta(report.verdict).label}</Tag>
              ) : (
                <Text type="secondary">—</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="标题" span={2}>
              {report.title}
            </Descriptions.Item>
          </Descriptions>
          <Divider style={{ margin: '12px 0' }} />
          <ReportContent report={report} />
          {report.status !== 'published' && (
            <Button
              style={{ marginTop: 12 }}
              type="primary"
              onClick={() => publishMutation.mutate(report.id)}
              loading={publishMutation.isPending}
            >
              发布报告
            </Button>
          )}
          <EvidenceList reportId={report.id} />
        </Card>
      ) : (
        <Alert type="info" message="暂无报告，任务完成后将自动生成" />
      ),
    },
  ]

  return (
    <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} type="card" />
  )
}

function OverviewTab({
  task,
  statusColor,
  statusLabel,
}: {
  task: TaskDetail
  statusColor: string
  statusLabel: string
}) {
  return (
    <div>
      {task.status === 'failed' && task.runs[0]?.error_message && (
        <Alert
          type="error"
          showIcon
          message="执行失败"
          description={task.runs[0].error_message}
          style={{ marginBottom: 16 }}
        />
      )}
      <Descriptions column={2} size="small" bordered>
        <Descriptions.Item label="项目地址" span={2}>
          <Text code>{task.project_address}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="引用">{task.project_ref ?? '默认分支'}</Descriptions.Item>
        <Descriptions.Item label="优先级">
          <Tag color={getPriorityMeta(task.priority).color}>{getPriorityMeta(task.priority).label}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={statusColor}>{statusLabel}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {dayjs(task.created_at).format('YYYY-MM-DD HH:mm:ss')}
        </Descriptions.Item>
        <Descriptions.Item label="漏洞描述" span={2}>
          <Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
            {task.vulnerability_description}
          </Paragraph>
        </Descriptions.Item>
      </Descriptions>
      {task.vulnerability_reasoning && (
        <>
          <Divider />
          <Title level={5}>分析推理</Title>
          <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{task.vulnerability_reasoning}</Paragraph>
        </>
      )}
    </div>
  )
}
