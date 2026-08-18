import { useEffect, useMemo, useState } from 'react'
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
import { useLocation } from 'wouter'

import type { TaskDetail } from '../../../shared/lib/api'
import { api } from '../../../shared/lib/api'
import { getStatusMeta, getPriorityMeta, getVerdictMeta, getReportStatusMeta, NODE_LABELS } from '../../../shared/lib/meta'
import { canCancel, canRetry, shouldFetchTaskReport } from '../../../shared/lib/taskActions'
import type { TaskDetailTab } from '../../../shared/lib/taskActions'
import { useTaskEvents, type SSEEvent } from '../../../shared/hooks/useTaskEvents'
import { dropNoisyEvents, eventsForRun, eventsForNode, mergeTaskEvents } from '../../../shared/lib/taskEvents'
import { NodeSteps } from '../../../shared/components/NodeSteps'
import { ReportContent } from '../../../shared/components/ReportContent'
import { EvidenceList } from './EvidenceList'
import { TaskEventTimeline } from './TaskEventTimeline'
import { FileTextOutlined } from '@ant-design/icons'

const { Title, Paragraph, Text } = Typography

interface TaskDetailTabsProps {
  taskId: string
  activeTab: TaskDetailTab
  onTabChange: (key: TaskDetailTab) => void
}

export function TaskDetailTabs({ taskId, activeTab, onTabChange }: TaskDetailTabsProps) {
  const { message, modal } = App.useApp()
  const qc = useQueryClient()
  const [, navigate] = useLocation()

  const { data: task, isLoading: taskLoading } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId),
  })

  const running = task ? canCancel(task.status) : false
  const sseEnabled = running
  const { events: sseEvents, status: sseStatus, error: sseError } = useTaskEvents(taskId, {
    enabled: sseEnabled,
  })

  const { data: restEvents } = useQuery({
    queryKey: ['task-events', taskId],
    queryFn: () => api.getTaskEvents(taskId),
    enabled: !!taskId,
  })

  useEffect(() => {
    const last = sseEvents[sseEvents.length - 1]
    if (!last) return
    if (last.type === 'agent.completed' || last.type === 'agent.failed') {
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['task-report', taskId] })
      qc.invalidateQueries({ queryKey: ['task-events', taskId] })
      qc.invalidateQueries({ queryKey: ['run-nodes', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
    }
  }, [sseEvents, qc, taskId])

  const events = useMemo(() => {
    const merged = mergeTaskEvents(restEvents, sseEvents as SSEEvent[])
    return dropNoisyEvents(eventsForRun(merged, task?.runs[0]?.id))
  }, [restEvents, sseEvents, task?.runs])

  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const nodeEvents = useMemo(() => eventsForNode(events, selectedNode), [events, selectedNode])

  const selectNode = (nodeKey: string) => {
    setSelectedNode((prev) => (prev === nodeKey ? null : nodeKey))
    onTabChange('events')
  }

  const { data: report } = useQuery({
    queryKey: ['task-report', taskId],
    queryFn: () => api.getReportByTask(taskId),
    enabled: !!taskId && shouldFetchTaskReport(task?.status ?? ''),
    retry: false,
  })

  const publishMutation = useMutation({
    mutationFn: (rid: string) => api.publishReport(rid),
    onSuccess: (published) => {
      message.success('报告已发布')
      qc.setQueryData(['task-report', taskId], published)
      qc.setQueryData(['report', published.id], published)
      qc.invalidateQueries({ queryKey: ['reports'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const retryFromNodeMutation = useMutation({
    mutationFn: (fromNode: string) => api.retryTask(taskId, fromNode),
    onSuccess: () => {
      message.success('已从该节点重新提交')
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['run-nodes', taskId] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const confirmRetryFromNode = (nodeKey: string) => {
    const label = NODE_LABELS[nodeKey] ?? nodeKey
    modal.confirm({
      title: `从「${label}」重试`,
      content: `将复用该节点之前的产出，只重跑「${label}」及之后的节点。确定继续？`,
      okText: '从本节点重试',
      cancelText: '返回',
      onOk: () => retryFromNodeMutation.mutate(nodeKey),
    })
  }

  if (taskLoading && !task) {
    return <Skeleton active paragraph={{ rows: 8 }} />
  }

  if (!task) {
    return <Alert type="error" title="任务不存在或无权访问" />
  }

  const st = getStatusMeta(task.status)
  const showPinnedNodes = !!task.runs[0]?.id

  const tabItems = [
    {
      key: 'overview',
      label: '概览',
      children: <OverviewTab task={task} statusColor={st.color ?? 'default'} statusLabel={st.label} />,
    },
    {
      key: 'progress',
      label: '进度',
      children: (
        <NodeSteps
          taskId={task.id}
          runId={task.runs[0]?.id}
          taskStatus={task.status}
          sseEvents={sseEvents as unknown as SSEEvent[]}
          selectedNode={selectedNode}
          onSelectNode={selectNode}
          onRetryFromNode={canRetry(task.status) ? confirmRetryFromNode : undefined}
        />
      ),
    },
    {
      key: 'events',
      label: '事件流',
      children: (
        <TaskEventTimeline
          events={nodeEvents}
          running={running}
          sseEnabled={sseEnabled}
          sseStatus={sseStatus}
          sseError={sseError}
          nodeLabel={selectedNode ? (NODE_LABELS[selectedNode] ?? selectedNode) : null}
          onClearNode={() => setSelectedNode(null)}
        />
      ),
    },
    {
      key: 'report',
      label: '报告',
      children: report ? (
        <Card variant="borderless">
          <Descriptions
            column={2}
            size="small"
            bordered
            items={[
              {
                key: 'status',
                label: '状态',
                children: (
                  <Tag color={getReportStatusMeta(report.status).color}>
                    {getReportStatusMeta(report.status).label}
                  </Tag>
                ),
              },
              {
                key: 'verdict',
                label: '判定',
                children: report.verdict ? (
                  <Tag color={getVerdictMeta(report.verdict).color}>{getVerdictMeta(report.verdict).label}</Tag>
                ) : (
                  <Text type="secondary">—</Text>
                ),
              },
              { key: 'title', label: '标题', span: 2, children: report.title },
            ]}
          />
          <Button
            type="link"
            icon={<FileTextOutlined />}
            style={{ paddingLeft: 0, marginTop: 8 }}
            onClick={() => navigate(`/reports/${report.id}`)}
          >
            打开全文阅读页
          </Button>
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
        <Alert type="info" title="暂无报告，任务完成后将自动生成" />
      ),
    },
  ]

  return (
    <div className="crucible-detail-body">
      {showPinnedNodes && (
        <div className="crucible-detail-nodes-pin">
          <NodeSteps
            taskId={task.id}
            runId={task.runs[0]?.id}
            taskStatus={task.status}
            sseEvents={sseEvents as unknown as SSEEvent[]}
            compact
            selectedNode={selectedNode}
            onSelectNode={selectNode}
          />
        </div>
      )}
      <Tabs
        className="crucible-fill-tabs"
        activeKey={activeTab}
        onChange={(key) => onTabChange(key as TaskDetailTab)}
        items={tabItems}
        type="card"
      />
    </div>
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
          title="执行失败"
          description={
            <Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
              {task.runs[0].error_message}
            </Paragraph>
          }
          style={{ marginBottom: 16 }}
        />
      )}
      <Descriptions
        column={2}
        size="small"
        bordered
        items={[
          {
            key: 'addr',
            label: '项目地址',
            span: 2,
            children: <Text code>{task.project_address}</Text>,
          },
          { key: 'ref', label: '引用', children: task.project_ref ?? '默认分支' },
          {
            key: 'priority',
            label: '优先级',
            children: (
              <Tag color={getPriorityMeta(task.priority).color}>{getPriorityMeta(task.priority).label}</Tag>
            ),
          },
          {
            key: 'status',
            label: '状态',
            children: <Tag color={statusColor}>{statusLabel}</Tag>,
          },
          {
            key: 'verdict',
            label: '判定',
            children: task.verdict ? (
              <Tag color={getVerdictMeta(task.verdict).color}>{getVerdictMeta(task.verdict).label}</Tag>
            ) : (
              <Text type="secondary">尚未判定</Text>
            ),
          },
          {
            key: 'created',
            label: '创建时间',
            span: 2,
            children: dayjs(task.created_at).format('YYYY-MM-DD HH:mm:ss'),
          },
          {
            key: 'desc',
            label: '漏洞描述',
            span: 2,
            children: (
              <Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
                {task.vulnerability_description}
              </Paragraph>
            ),
          },
        ]}
      />
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
