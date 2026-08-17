import { useEffect, useMemo } from 'react'
import { Steps, Typography } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  ClockCircleOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api, type NodeRun } from '../lib/api'
import type { SSEEvent } from '../hooks/useTaskEvents'
import { NODE_LABELS, NODE_STATUS_META } from '../lib/meta'
import {
  applyNodeOverlay,
  compactNodeCaption,
  displayNodeStatus,
  formatAuditDetail,
  isNodeListLoading,
  isNodeTerminal,
  overlayFromSseEvents,
  summarizeNodeOutput,
} from '../lib/nodeOutput'

const { Text } = Typography

const NODE_ORDER: NodeRun['node_key'][] = ['source', 'profile', 'env_ready', 'audit', 'reproduce', 'report']

interface NodeStepsProps {
  taskId: string
  runId: string | null | undefined
  taskStatus?: string
  sseEvents?: SSEEvent[]
  compact?: boolean
}

function nodeIcon(status: NodeRun['status']) {
  if (status === 'completed') return <CheckCircleOutlined style={{ color: 'var(--crucible-success)' }} />
  if (status === 'failed') return <CloseCircleOutlined style={{ color: 'var(--crucible-error)' }} />
  if (status === 'cancelled') return <StopOutlined style={{ color: 'var(--crucible-text-disabled)' }} />
  if (status === 'running') return <LoadingOutlined />
  if (status === 'skipped') return <MinusCircleOutlined style={{ color: 'var(--crucible-text-disabled)' }} />
  return <ClockCircleOutlined />
}

function nodeSummary(n: Pick<NodeRun, 'node_key' | 'status' | 'output' | 'error_message'>): string {
  if (n.status === 'failed' && n.error_message) return n.error_message
  if (n.node_key === 'audit') return formatAuditDetail(n.output, n.status)
  return summarizeNodeOutput(n.node_key, n.output, n.status)
}

export function NodeSteps({ taskId, runId, taskStatus, sseEvents = [], compact = false }: NodeStepsProps) {
  const { data: nodes, refetch } = useQuery({
    queryKey: ['run-nodes', taskId, runId],
    queryFn: () => api.getRunNodes(taskId, runId!),
    enabled: !!taskId && !!runId,
    refetchInterval: (query) => {
      if (taskStatus === 'cancelled') return false
      const ns = query.state.data
      if (ns && ns.length === 6 && ns.every((n) => isNodeTerminal(n.status))) {
        return false
      }
      return 3000
    },
  })

  const lastNodeUpdate = useMemo(() => {
    for (let i = sseEvents.length - 1; i >= 0; i--) {
      if (sseEvents[i].type === 'node.updated') return sseEvents[i]
    }
    return null
  }, [sseEvents])

  useEffect(() => {
    if (lastNodeUpdate) refetch()
  }, [lastNodeUpdate, refetch])

  const sseOverlay = useMemo(() => overlayFromSseEvents(sseEvents), [sseEvents])

  if (!runId) {
    return <Text type="secondary">尚无运行记录</Text>
  }

  if (isNodeListLoading(nodes)) {
    return <Text type="secondary">节点状态加载中...</Text>
  }

  const nodeMap = new Map(nodes.map((n) => [n.node_key, n]))
  const ordered = NODE_ORDER.map((key, idx) => {
    const n = nodeMap.get(key)
    const base: NodeRun = n ?? {
      id: `pending-${key}`,
      node_index: idx,
      node_key: key,
      status: 'pending',
      attempt: 0,
      error_message: null,
      started_at: null,
      finished_at: null,
      output: {},
    }
    const over = sseOverlay.get(key)
    const merged = over
      ? {
          ...base,
          ...over,
          node_key: key,
          output: { ...(base.output ?? {}), ...(over.output ?? {}) },
          status: applyNodeOverlay(base, over) as NodeRun['status'],
        }
      : base
    const status = displayNodeStatus(merged.status, taskStatus) as NodeRun['status']
    return { ...merged, status }
  })

  const currentIdx = ordered.findIndex((n) => n.status === 'running')
  const firstFailed = ordered.findIndex((n) => n.status === 'failed')

  if (compact) {
    const items = ordered.map((n) => {
      const meta = NODE_STATUS_META[n.status] ?? NODE_STATUS_META.pending
      const caption = compactNodeCaption(n.node_key, n.output, n.status)
      return {
        title: NODE_LABELS[n.node_key] ?? n.node_key,
        content: caption || undefined,
        status: meta.status as 'wait' | 'process' | 'finish' | 'error',
        icon: nodeIcon(n.status),
      }
    })
    return (
      <Steps
        size="small"
        ellipsis
        className="crucible-node-steps-compact"
        classNames={{
          itemTitle: 'crucible-node-steps-compact__title',
          itemContent: 'crucible-node-steps-compact__content',
        }}
        current={currentIdx >= 0 ? currentIdx : firstFailed >= 0 ? firstFailed : ordered.length}
        items={items}
        orientation="horizontal"
      />
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
      {ordered.map((n) => {
        const summary = nodeSummary(n)
        const failed = n.status === 'failed'
        return (
          <div
            key={n.id}
            style={{
              display: 'grid',
              gridTemplateColumns: '24px 96px 1fr',
              gap: 12,
              alignItems: 'start',
              padding: '10px 12px',
              border: '1px solid var(--crucible-border)',
              borderRadius: 8,
              background: n.status === 'running' ? 'var(--crucible-bg)' : undefined,
            }}
          >
            <span style={{ lineHeight: '22px' }}>{nodeIcon(n.status)}</span>
            <Text strong>
              {NODE_LABELS[n.node_key] ?? n.node_key}
              {n.attempt > 1 && (
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {' '}
                  · 第 {n.attempt} 次
                </Text>
              )}
            </Text>
            <Text
              type={failed ? 'danger' : 'secondary'}
              style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}
            >
              {summary}
            </Text>
          </div>
        )
      })}
    </div>
  )
}
