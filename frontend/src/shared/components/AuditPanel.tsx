import { Card } from 'antd'
import { useQuery } from '@tanstack/react-query'

import { api } from '../lib/api'
import { AuditDetail } from './AuditDetail'

interface AuditPanelProps {
  taskId: string
  runId: string
}

/**
 * 报告侧的白盒审计原始结论，与进度页共用 AuditDetail 排版。
 * queryKey 与 NodeSteps 一致，命中同一份缓存。
 */
export function AuditPanel({ taskId, runId }: AuditPanelProps) {
  const { data: nodes } = useQuery({
    queryKey: ['run-nodes', taskId, runId],
    queryFn: () => api.getRunNodes(taskId, runId),
    enabled: !!taskId && !!runId,
    staleTime: 60_000,
  })

  const audit = nodes?.find((n) => n.node_key === 'audit')
  if (!audit || audit.status !== 'completed') return null

  return (
    <Card size="small" title="白盒审计结论">
      <AuditDetail output={audit.output} />
    </Card>
  )
}
