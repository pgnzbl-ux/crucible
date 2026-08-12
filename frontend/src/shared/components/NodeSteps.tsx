import { useEffect, useMemo } from 'react'
import { Steps, Tag, Tooltip, Typography } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api, type NodeRun } from '../lib/api'
import type { SSEEvent } from '../hooks/useTaskEvents'
import { NODE_LABELS, NODE_STATUS_META } from '../lib/meta'

const { Text } = Typography

const NODE_ORDER: NodeRun['node_key'][] = ['source', 'profile', 'env_ready', 'audit', 'reproduce', 'report']

interface NodeStepsProps {
  taskId: string
  runId: string | null | undefined
  /** SSE 事件流(含 node.updated),用于实时驱动状态 */
  sseEvents?: SSEEvent[]
}

/**
 * 6 节点步骤条 — 展示编排进度。
 *
 * 数据源:
 *  1. getRunNodes(taskId, runId) 拉历史/初始状态
 *  2. sseEvents 里的 node.updated 实时覆盖
 */
export function NodeSteps({ taskId, runId, sseEvents = [] }: NodeStepsProps) {
  // 拉历史节点状态(runId 变化或 node.updated 触发时刷新)
  const { data: nodes, refetch } = useQuery({
    queryKey: ['run-nodes', taskId, runId],
    queryFn: () => api.getRunNodes(taskId, runId!),
    enabled: !!taskId && !!runId,
    refetchInterval: (query) => {
      // 节点全终态后停止轮询(SSE 不可用时的兜底)
      const ns = query.state.data
      if (ns && ns.length === 6 && ns.every((n) => ['completed', 'failed', 'skipped'].includes(n.status))) {
        return false
      }
      return 3000
    },
  })

  // SSE node.updated 事件 → 触发 refetch(拿最新持久化状态)
  const lastNodeUpdate = useMemo(() => {
    for (let i = sseEvents.length - 1; i >= 0; i--) {
      if (sseEvents[i].type === 'node.updated') return sseEvents[i]
    }
    return null
  }, [sseEvents])

  useEffect(() => {
    if (lastNodeUpdate) refetch()
  }, [lastNodeUpdate, refetch])

  if (!runId) {
    return <Text type="secondary">尚无运行记录</Text>
  }

  if (!nodes || nodes.length === 0) {
    return <Text type="secondary">节点状态加载中...</Text>
  }

  // 按 node_key 补齐 6 节点(后端可能只建了部分)
  const nodeMap = new Map(nodes.map((n) => [n.node_key, n]))
  const ordered = NODE_ORDER.map((key, idx) => {
    const n = nodeMap.get(key)
    return (
      n ?? {
        id: `pending-${key}`,
        node_index: idx,
        node_key: key,
        status: 'pending' as const,
        attempt: 0,
        error_message: null,
        started_at: null,
        finished_at: null,
      }
    )
  })

  const currentIdx = ordered.findIndex((n) => n.status === 'running')
  const firstFailed = ordered.findIndex((n) => n.status === 'failed')

  const items = ordered.map((n) => {
    const meta = NODE_STATUS_META[n.status] ?? NODE_STATUS_META.pending
    let icon
    if (n.status === 'completed') icon = <CheckCircleOutlined style={{ color: 'var(--crucible-success)' }} />
    else if (n.status === 'failed') icon = <CloseCircleOutlined style={{ color: 'var(--crucible-error)' }} />
    else if (n.status === 'running') icon = <LoadingOutlined />
    else if (n.status === 'skipped') icon = <MinusCircleOutlined style={{ color: 'var(--crucible-text-disabled)' }} />
    else icon = <ClockCircleOutlined />

    const title = (
      <span>
        {NODE_LABELS[n.node_key] ?? n.node_key}
        {n.attempt > 1 && <Text type="secondary" style={{ fontSize: 11 }}> · 第 {n.attempt} 次</Text>}
      </span>
    )
    const desc = n.error_message ? (
      <Tooltip title={n.error_message}>
        <Text type="danger" style={{ fontSize: 11 }}>失败(悬停查看)</Text>
      </Tooltip>
    ) : n.status === 'skipped' ? (
      <Text type="secondary" style={{ fontSize: 11 }}>跳过</Text>
    ) : n.status === 'completed' && n.finished_at ? (
      <Text type="secondary" style={{ fontSize: 11 }}>完成</Text>
    ) : null

    return {
      title,
      description: desc,
      status: meta.status as 'wait' | 'process' | 'finish' | 'error',
      icon,
    }
  })

  return (
    <Steps
      size="small"
      current={currentIdx >= 0 ? currentIdx : firstFailed >= 0 ? firstFailed : ordered.length}
      items={items}
      direction="vertical"
      style={{ marginTop: 8 }}
    />
  )
}
