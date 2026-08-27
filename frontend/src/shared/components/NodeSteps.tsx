import { useMemo, useState } from 'react'
import { Button, Typography } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  ClockCircleOutlined,
  ExpandOutlined,
  CompressOutlined,
  RedoOutlined,
  StopOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { api, type NodeRun } from '../lib/api'
import type { SSEEvent, SSEStatus } from '../hooks/useTaskEvents'
import { NODE_LABELS, PIPELINE_NODE_ORDER, VERIFY_MODE_SKIPPED_KEYS, formatTokenCount, isAiNode, mergeTokenUsage } from '../lib/meta'
import {
  applyNodeOverlay,
  displayNodeStatus,
  formatDuration,
  isNodeListLoading,
  isNodeSelectable,
  nodeMetrics,
  overlayFromSseEvents,
  skipReasonLabel,
  summarizeNodeOutput,
  nodeStepsPollMs,
} from '../lib/nodeOutput'
import {
  DAG_STATUS_TEXT,
  dagVisualStatus,
  DISCOVERY_REPLACED_NODE_KEYS,
  pipelineOverviewStages,
  type DagVisualStatus,
  type PipelineMode,
} from '../lib/pipelineDag'
import { AuditDetail } from './AuditDetail'
import { EnvReadyDetail } from './EnvReadyDetail'
import { LeadVerifyDetail } from './LeadVerifyDetail'
import { NodeDag } from './NodeDag'
import { NodeOutputDetail } from './NodeOutputDetail'
import { canRetryFromNode } from '../lib/taskActions'

const { Text } = Typography

interface NodeStepsProps {
  taskId: string
  runId: string | null | undefined
  taskStatus?: string
  taskType?: 'verify' | 'discovery'
  sseEvents?: SSEEvent[]
  sseStatus?: SSEStatus
  compact?: boolean
  expanded?: boolean
  onToggleExpand?: () => void
  selectedNode?: string | null
  onSelectNode?: (nodeKey: NodeRun['node_key']) => void
  onRetryFromNode?: (nodeKey: NodeRun['node_key']) => void
}

function nodeSummary(n: Pick<NodeRun, 'node_key' | 'status' | 'output' | 'error_message'>): string {
  if (n.error_message) return n.error_message
  if (n.node_key === 'lead_verify') {
    const count = typeof n.output?.queued_count === 'number'
      ? n.output.queued_count
      : typeof n.output?.lead_count === 'number'
        ? n.output.lead_count
        : null
    if (n.status === 'running') return count == null ? '正在逐条终认线索' : `正在终认 ${count} 条线索`
    if (n.status === 'completed') return count == null ? '线索终认完成' : `${count} 条线索终认完成`
    if (n.status === 'skipped') return '没有高置信线索，已跳过终认'
  }
  return summarizeNodeOutput(n.node_key, n.output, n.status)
}

/** 完成的 audit / env_ready / lead_verify 有结构化面板；其余节点指标卡片，无指标再退回一句摘要。 */
function nodeDetail(n: Pick<NodeRun, 'node_key' | 'status' | 'output'>) {
  if (n.node_key === 'audit' && n.status === 'completed') return <AuditDetail output={n.output} />
  if (n.node_key === 'env_ready' && n.status === 'completed') return <EnvReadyDetail output={n.output} />
  if (n.node_key === 'lead_verify' && (n.status === 'completed' || n.status === 'skipped')) {
    return <LeadVerifyDetail output={n.output} />
  }
  return null
}

function listIcon(status: DagVisualStatus) {
  if (status === 'completed') return <CheckCircleOutlined />
  if (status === 'failed') return <CloseCircleOutlined />
  if (status === 'degraded') return <WarningOutlined />
  if (status === 'cancelled') return <StopOutlined />
  if (status === 'running') return <LoadingOutlined />
  if (status === 'skipped' || status === 'blocked') return <MinusCircleOutlined />
  return <ClockCircleOutlined />
}

/** 失败任务中仍 pending 的节点没有执行：用独立灰态表达依赖阻断。 */
function nodeVisualStatus(
  node: Pick<NodeRun, 'status' | 'output' | 'error_message'>,
  taskStatus?: string,
): DagVisualStatus {
  if (taskStatus === 'failed' && node.status === 'pending') return 'blocked'
  return dagVisualStatus(node)
}

function presentationStatus(node: Pick<NodeRun, 'status'>, taskStatus?: string): string {
  return taskStatus === 'failed' && node.status === 'pending' ? 'blocked' : node.status
}

function discoveryLeadStatus(
  dispatch: Pick<NodeRun, 'status' | 'output'> | undefined,
  finalize: Pick<NodeRun, 'status'> | undefined,
  taskStatus?: string,
): NodeRun['status'] {
  if (!dispatch || dispatch.status === 'pending' || dispatch.status === 'running') return 'pending'
  if (dispatch.status === 'failed') return 'pending'
  if (dispatch.status === 'cancelled') return 'cancelled'
  if (dispatch.status === 'skipped') return 'skipped'

  const hasLead = dispatch.output?.has_lead
  const queuedCount = dispatch.output?.queued_count
  if (typeof hasLead !== 'boolean' && typeof queuedCount !== 'number') return 'pending'
  if (hasLead !== true && !(typeof queuedCount === 'number' && queuedCount > 0)) return 'skipped'

  if (!finalize || finalize.status === 'pending') {
    if (taskStatus === 'cancelled') return 'cancelled'
    if (taskStatus === 'failed') return 'pending'
    return 'running'
  }
  if (finalize.status === 'cancelled') return 'cancelled'
  // finalize 在 LeadWorker 排空后才会进入 running/terminal。
  return 'completed'
}

export function NodeSteps({
  taskId,
  runId,
  taskStatus,
  taskType = 'verify',
  sseEvents = [],
  sseStatus,
  compact = false,
  expanded = false,
  onToggleExpand,
  selectedNode = null,
  onSelectNode,
  onRetryFromNode,
}: NodeStepsProps) {
  const { data: nodes } = useQuery({
    queryKey: ['run-nodes', taskId, runId],
    queryFn: () => api.getRunNodes(taskId, runId!),
    enabled: !!taskId && !!runId,
    refetchInterval: (query) =>
      nodeStepsPollMs({
        taskStatus,
        nodes: query.state.data,
        sseLive: sseStatus === 'open',
      }),
  })

  const sseOverlay = useMemo(() => overlayFromSseEvents(sseEvents), [sseEvents])
  const [showSkipped, setShowSkipped] = useState(false)

  if (!runId) {
    return <Text type="secondary">尚无运行记录</Text>
  }

  if (isNodeListLoading(nodes)) {
    return <Text type="secondary">节点状态加载中...</Text>
  }

  const nodeMap = new Map(nodes.map((n) => [n.node_key, n]))
  const renderOrder = [...PIPELINE_NODE_ORDER]
  const hiddenCount =
    taskType === 'verify' ? renderOrder.filter((key) => VERIFY_MODE_SKIPPED_KEYS.has(key)).length : 0
  const visibleOrder =
    taskType === 'verify' && !showSkipped
      ? renderOrder.filter((key) => !VERIFY_MODE_SKIPPED_KEYS.has(key))
      : renderOrder
  const ordered = visibleOrder.map((key, idx) => {
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
  const mode: PipelineMode = taskType === 'discovery' ? 'discovery' : 'verify'
  const dispatchNode = ordered.find((n) => n.node_key === 'dispatch')
  const finalizeNode = ordered.find((n) => n.node_key === 'finalize')
  const reportNode = ordered.find((n) => n.node_key === 'report')
  const auditNode = ordered.find((n) => n.node_key === 'audit')
  const reproduceNode = ordered.find((n) => n.node_key === 'reproduce')
  const leadVerifyRuntime = nodeMap.get('lead_verify')
  const progressOrdered: NodeRun[] = mode === 'discovery'
    ? ordered
        .filter((n) => !DISCOVERY_REPLACED_NODE_KEYS.has(n.node_key))
        .map((n) => {
          if (n.node_key !== 'lead_verify') return n
          return {
            ...n,
            id: leadVerifyRuntime?.id ?? n.id,
            status: (leadVerifyRuntime?.status
              ?? (n.status !== 'pending' ? n.status : discoveryLeadStatus(dispatchNode, finalizeNode, taskStatus))) as NodeRun['status'],
            attempt: leadVerifyRuntime?.attempt ?? n.attempt,
            error_message: leadVerifyRuntime?.error_message ?? n.error_message,
            started_at: leadVerifyRuntime?.started_at ?? n.started_at,
            finished_at: leadVerifyRuntime?.finished_at ?? n.finished_at,
            output: {
              queued_count: dispatchNode?.output?.queued_count,
              ...(n.output ?? {}),
              ...(leadVerifyRuntime?.output ?? {}),
            },
            usage: leadVerifyRuntime?.usage
              ?? n.usage
              ?? mergeTokenUsage(auditNode?.usage, reproduceNode?.usage),
          }
        })
    : ordered

  const skipToggle =
    hiddenCount > 0 ? (
      <Button
        size="small"
        type="link"
        style={{ paddingLeft: 0, alignSelf: 'flex-start' }}
        onClick={() => setShowSkipped((v) => !v)}
      >
        {showSkipped ? '收起跳过的节点' : `显示跳过的节点(${hiddenCount})`}
      </Button>
    ) : null

  if (compact) {
    const dagNodes = progressOrdered
      .map((n) => ({
        key: n.node_key,
        status: presentationStatus(n, taskStatus),
        error_message: n.error_message,
        output: n.output,
        usage: n.usage ?? null,
        started_at: n.started_at,
        finished_at: n.finished_at,
        selectable: !!onSelectNode && isNodeSelectable(n.status),
        selected: selectedNode === n.node_key,
      }))
    const overStatus: NodeRun['status'] =
      reportNode && ['completed', 'failed', 'cancelled'].includes(reportNode.status)
        ? reportNode.status
        : taskStatus === 'failed' || taskStatus === 'cancelled'
          ? taskStatus
          : 'pending'
    dagNodes.push({
      key: 'over',
      status: overStatus,
      error_message: null,
      output: {},
      usage: null,
      started_at: null,
      finished_at: null,
      selectable: false,
      selected: false,
    })
    return (
      <div className="crucible-node-flow is-compact">
        <div className="crucible-flow-canvas__toolbar">
          {skipToggle}
          {onToggleExpand ? (
            <Button
              size="small"
              type="link"
              icon={expanded ? <CompressOutlined /> : <ExpandOutlined />}
              onClick={onToggleExpand}
            >
              {expanded ? '收起流程图' : '展开流程图'}
            </Button>
          ) : null}
        </div>
        <div className="crucible-node-flow__dag">
          <NodeDag
            nodes={dagNodes}
            contain={!expanded}
            overview={!expanded}
            mode={mode}
            onSelect={onSelectNode}
          />
        </div>
      </div>
    )
  }

  const stages = pipelineOverviewStages(mode)
  const terminalCount = progressOrdered.filter((n) => {
    const status = nodeVisualStatus(n, taskStatus)
    return status !== 'pending' && status !== 'running' && status !== 'blocked'
  }).length
  const progressPercent = progressOrdered.length > 0
    ? Math.round((terminalCount / progressOrdered.length) * 100)
    : 0
  const activeNode = progressOrdered.find((n) => nodeVisualStatus(n, taskStatus) === 'running')
  const blockedNode = progressOrdered.find((n) => {
    const status = nodeVisualStatus(n, taskStatus)
    return status === 'failed' || status === 'degraded' || status === 'cancelled'
  })
  const progressMessage = activeNode
    ? `正在执行：${NODE_LABELS[activeNode.node_key] ?? activeNode.node_key}`
    : blockedNode
      ? `流程停在：${NODE_LABELS[blockedNode.node_key] ?? blockedNode.node_key}`
      : taskStatus === 'failed'
        ? '流程已终止，下游节点未执行'
        : terminalCount === progressOrdered.length && progressOrdered.length > 0
          ? '全部节点已结束'
          : '等待下一节点就绪'

  return (
    <div className="crucible-node-flow crucible-node-progress">
      <div className="crucible-node-progress__summary">
        <div className="crucible-node-progress__summary-copy">
          <span className="crucible-node-progress__eyebrow">
            {mode === 'discovery' ? '仓库审计执行链' : '定向验证执行链'}
          </span>
          <strong>{progressMessage}</strong>
          <small>已结束 {terminalCount} / {progressOrdered.length} 个节点</small>
        </div>
        <span className="crucible-node-progress__percent">{progressPercent}%</span>
        <div
          className="crucible-node-progress__bar"
          role="progressbar"
          aria-label="审计节点进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progressPercent}
        >
          <span style={{ width: `${progressPercent}%` }} />
        </div>
      </div>
      <div className="crucible-node-progress__toolbar">{skipToggle}</div>
      <div className="crucible-node-list">
        {progressOrdered.map((n, index) => {
          const visual = nodeVisualStatus(n, taskStatus)
          const selectable = Boolean(onSelectNode && isNodeSelectable(n.status) && visual !== 'blocked')
          const stageIndex = stages.findIndex((stage) => stage.nodeKeys.includes(n.node_key))
          const stage = stageIndex >= 0 ? stages[stageIndex] : null
          const parallel = Boolean(stage?.parallel)
          const duration = formatDuration(n.started_at, n.finished_at)
          const skipReason = visual === 'skipped' ? skipReasonLabel(n, taskType) : ''
          const detail = nodeDetail(n)
          const hasMetrics = nodeMetrics(n).length > 0
          return (
            <div
              key={n.node_key}
              className={[
                'crucible-node-list__item',
                `is-${visual}`,
                selectedNode === n.node_key ? 'is-selected' : '',
                selectable ? 'is-selectable' : '',
              ].filter(Boolean).join(' ')}
              data-node-key={n.node_key}
              role={selectable ? 'button' : undefined}
              tabIndex={selectable ? 0 : undefined}
              aria-label={selectable ? `查看${NODE_LABELS[n.node_key] ?? n.node_key}运行日志` : undefined}
              onClick={selectable ? () => onSelectNode?.(n.node_key) : undefined}
              onKeyDown={selectable ? (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onSelectNode?.(n.node_key)
                }
              } : undefined}
            >
              <div className="crucible-node-list__rail" aria-hidden>
                <span className="crucible-node-list__icon">{listIcon(visual)}</span>
              </div>
              <div className="crucible-node-list__card">
                <div className="crucible-node-list__head">
                  <div className="crucible-node-list__title">
                    <span className="crucible-node-list__stage">
                      {stage ? `阶段 ${String(stageIndex + 1).padStart(2, '0')} · ${stage.label}` : '内部跳过链'}
                      {parallel ? <em>并行</em> : null}
                    </span>
                    <strong>
                      {NODE_LABELS[n.node_key] ?? n.node_key}
                      {isAiNode(n.node_key) ? (
                        <span className="crucible-node-list__ai" title="AI 节点">AI</span>
                      ) : null}
                    </strong>
                  </div>
                  <div className="crucible-node-list__meta">
                    {n.usage && n.usage.total_tokens > 0 ? (
                      <span
                        className="crucible-node-list__tokens"
                        title={[
                          `prompt ${n.usage.prompt_tokens}`,
                          `completion ${n.usage.completion_tokens}`,
                          `cache_read ${n.usage.cache_read_input_tokens}`,
                          `cache_creation ${n.usage.cache_creation_input_tokens}`,
                        ].join(' · ')}
                      >
                        {formatTokenCount(n.usage.total_tokens)} tok
                      </span>
                    ) : null}
                    {duration ? (
                      <span className="crucible-node-list__duration">
                        {visual === 'running' ? `已进行 ${duration}` : duration}
                      </span>
                    ) : null}
                    {n.attempt > 1 ? <span>第 {n.attempt} 次</span> : null}
                    <span className={`crucible-node-list__status is-${visual}`}>{DAG_STATUS_TEXT[visual]}</span>
                  </div>
                </div>
                {visual === 'blocked' ? (
                  <p className="crucible-node-list__summary">上游必要节点失败，本节点未执行</p>
                ) : visual === 'running' ? null : n.error_message ? (
                  <pre className="crucible-node-error-log">{n.error_message}</pre>
                ) : (
                  <>
                    {detail ?? (hasMetrics ? (
                      <NodeOutputDetail node={n} />
                    ) : (
                      <p className="crucible-node-list__summary">
                        {skipReason ? `跳过 · ${skipReason}` : nodeSummary(n)}
                      </p>
                    ))}
                    {detail && skipReason ? (
                      <p className="crucible-node-list__summary">跳过 · {skipReason}</p>
                    ) : null}
                  </>
                )}
                {!!onRetryFromNode && canRetryFromNode(taskStatus ?? '', n.node_key, n.status) && (
                  <Button
                    size="small"
                    type="link"
                    icon={<RedoOutlined />}
                    className="crucible-node-list__retry"
                    onClick={(event) => {
                      event.stopPropagation()
                      onRetryFromNode(n.node_key)
                    }}
                    onKeyDown={(event) => event.stopPropagation()}
                  >
                    从本节点重试
                  </Button>
                )}
              </div>
              {index < progressOrdered.length - 1 ? <span className="crucible-node-list__connector" aria-hidden /> : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}
