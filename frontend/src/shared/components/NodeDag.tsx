import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  ClockCircleOutlined,
  StopOutlined,
  WarningOutlined,
} from '@ant-design/icons'

import { NODE_LABELS, formatTokenCount, isAiNode, mergeTokenUsage } from '../lib/meta'
import {
  DAG_STATUS_TEXT,
  dagVisualStatus,
  fitGraphToView,
  layoutPipelineDag,
  pipelineOverviewStages,
  type DagVisualStatus,
  type PipelineMode,
} from '../lib/pipelineDag'

export interface DagNodeModel {
  key: string
  status: string
  error_message?: string | null
  output?: Record<string, unknown> | null
  usage?: {
    prompt_tokens: number
    completion_tokens: number
    cache_read_input_tokens: number
    cache_creation_input_tokens: number
    total_tokens: number
  } | null
  selectable?: boolean
  selected?: boolean
}

interface NodeDagProps {
  nodes: DagNodeModel[]
  mode?: PipelineMode
  contain?: boolean
  overview?: boolean
  onSelect?: (key: string) => void
}

const DEFAULT_VIEW = { width: 1100, height: 128 }

function statusIcon(status: DagVisualStatus): ReactNode {
  if (status === 'completed') return <CheckCircleOutlined />
  if (status === 'failed') return <CloseCircleOutlined />
  if (status === 'degraded') return <WarningOutlined />
  if (status === 'cancelled') return <StopOutlined />
  if (status === 'running') return <LoadingOutlined />
  if (status === 'skipped' || status === 'blocked') return <MinusCircleOutlined />
  return <ClockCircleOutlined />
}

const NODE_CAPTIONS: Record<string, string> = {
  source: '获取或复用仓库',
  profile: '语言、框架与 Web 属性',
  scan_gitleaks: '敏感信息检测',
  scan_osv: '依赖漏洞检测',
  scan_semgrep: '静态代码检测',
  env_ready: '仅 Web 构建靶场',
  api_inventory: '确定性入口清单',
  api_hunt: '鉴权/逻辑候选猎洞',
  cluster: '发现归并与去重',
  screen: '规则/快审过滤',
  triage: 'Agent 亲审',
  dispatch: '筛选高置信线索',
  audit: '白盒路径终认',
  reproduce: '动态验证利用链',
  lead_verify: '逐线索审计 / 复现',
  finalize: '固化分析结论',
  report: '聚合证据与文档',
}

function aggregateStatus(models: readonly DagNodeModel[]): DagVisualStatus {
  const statuses = models.map(dagVisualStatus)
  if (statuses.some((status) => status === 'failed')) return 'failed'
  if (statuses.some((status) => status === 'degraded')) return 'degraded'
  if (statuses.some((status) => status === 'running')) return 'running'
  if (statuses.some((status) => status === 'cancelled')) return 'cancelled'
  if (statuses.some((status) => status === 'blocked')) return 'blocked'
  if (statuses.length > 0 && statuses.every((status) => status === 'skipped')) return 'skipped'
  if (statuses.length > 0 && statuses.every((status) => status === 'completed' || status === 'skipped')) {
    return 'completed'
  }
  return 'pending'
}

function nodeCaption(key: string, output?: Record<string, unknown> | null): string {
  if (key === 'profile') {
    const language = typeof output?.language === 'string' ? output.language : ''
    const web = output?.is_web === true ? 'Web' : output?.is_web === false ? '非 Web' : ''
    if (language || web) return [language, web].filter(Boolean).join(' · ')
  }
  if (key.startsWith('scan_') && typeof output?.finding_count === 'number') {
    return `${output.finding_count} 条发现`
  }
  if (key === 'cluster') {
    const groups = typeof output?.group_count === 'number' ? output.group_count : null
    const dropped = typeof output?.dropped_c_count === 'number' ? output.dropped_c_count : null
    if (groups != null && dropped != null && dropped > 0) {
      return `${groups} 组 · C档丢弃 ${dropped}`
    }
    if (groups != null) return `${groups} 组`
    if (dropped != null && dropped > 0) return `C档丢弃 ${dropped}`
  }
  if ((key === 'dispatch' || key === 'lead_verify') && typeof output?.queued_count === 'number') {
    return `${output.queued_count} 条线索`
  }
  if (key === 'api_inventory' && typeof output?.endpoint_count === 'number') {
    const parsers = Array.isArray(output.parsers)
      ? output.parsers.filter((p): p is string => typeof p === 'string' && p.length > 0 && p !== 'openapi')
      : []
    const label = parsers.slice(0, 2).join('/')
      || (typeof output.parser === 'string' && output.parser !== 'none' ? output.parser : '')
    return label ? `${output.endpoint_count} 端点 · ${label}` : `${output.endpoint_count} 端点`
  }
  return NODE_CAPTIONS[key] ?? ''
}

function DagOverview({
  nodes,
  mode,
  onSelect,
}: {
  nodes: DagNodeModel[]
  mode: PipelineMode
  onSelect?: (key: string) => void
}) {
  const byKey = new Map(nodes.map((node) => [node.key, node]))
  const stages = pipelineOverviewStages(mode)
  const totalUsage = mergeTokenUsage(
    ...nodes.filter((node) => node.key !== 'over').map((node) => node.usage),
  )
  return (
    <div className="crucible-dag-overview" role="group" aria-label="任务业务阶段流程图">
      <div className="crucible-dag-overview__mode">
        {mode === 'discovery' ? '仓库审计' : '定向验证'}
        {totalUsage && totalUsage.total_tokens > 0 ? (
          <span
            className="crucible-dag-overview__total"
            title={[
              `prompt ${totalUsage.prompt_tokens}`,
              `completion ${totalUsage.completion_tokens}`,
              `cache_read ${totalUsage.cache_read_input_tokens}`,
              `cache_creation ${totalUsage.cache_creation_input_tokens}`,
            ].join(' · ')}
          >
            {formatTokenCount(totalUsage.total_tokens)} tok
          </span>
        ) : null}
      </div>
      <div className="crucible-dag-overview__track">
        {stages.map((stage, index) => {
          const models = stage.nodeKeys.flatMap((key) => {
            const model = byKey.get(key)
            return model ? [model] : []
          })
          const visual = aggregateStatus(models)
          const terminalCount = models.filter((model) => {
            const status = dagVisualStatus(model)
            return status === 'completed' || status === 'skipped'
          }).length
          const selectableModels = models.filter((model) => model.selectable)
          const target = models.find((model) => model.selected)
            ?? models.find((model) => ['running', 'failed', 'degraded'].includes(dagVisualStatus(model)))
            ?? selectableModels[selectableModels.length - 1]
          const selectable = Boolean(onSelect && target?.selectable)
          const stageIsAi = stage.nodeKeys.some((key) => isAiNode(key))
          const stageUsage = mergeTokenUsage(...models.map((model) => model.usage))
          return (
            <div className="crucible-dag-overview__segment" key={stage.key}>
              <button
                type="button"
                className={[
                  'crucible-dag-stage-card',
                  `is-${visual}`,
                  models.some((model) => model.selected) ? 'is-selected' : '',
                  selectable ? 'is-selectable' : '',
                  stageIsAi ? 'is-ai' : '',
                ].filter(Boolean).join(' ')}
                data-stage-key={stage.key}
                data-ai={stageIsAi ? 'true' : undefined}
                disabled={!selectable}
                onClick={selectable ? () => onSelect?.(target!.key) : undefined}
              >
                {stageIsAi ? (
                  <span className="crucible-dag-stage-card__ai" title="含 AI 节点">AI</span>
                ) : null}
                <span className="crucible-dag-stage-card__index">{index + 1}</span>
                <span className="crucible-dag-stage-card__copy">
                  <strong>{stage.label}</strong>
                  <small>{stage.caption}</small>
                  {stageUsage && stageUsage.total_tokens > 0 ? (
                    <span
                      className="crucible-dag-stage-card__tokens"
                      title={[
                        `prompt ${stageUsage.prompt_tokens}`,
                        `completion ${stageUsage.completion_tokens}`,
                        `cache_read ${stageUsage.cache_read_input_tokens}`,
                        `cache_creation ${stageUsage.cache_creation_input_tokens}`,
                      ].join(' · ')}
                    >
                      {formatTokenCount(stageUsage.total_tokens)} tok
                    </span>
                  ) : null}
                </span>
                <span className="crucible-dag-stage-card__state" title={DAG_STATUS_TEXT[visual]}>
                  {statusIcon(visual)}
                  {models.length > 1 ? <em>{terminalCount}/{models.length}</em> : null}
                </span>
              </button>
              {index < stages.length - 1 ? <span className="crucible-dag-overview__arrow" aria-hidden>›</span> : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function NodeDag({ nodes, mode = 'discovery', contain = true, overview = false, onSelect }: NodeDagProps) {
  const gid = useId().replace(/:/g, '')
  const hostRef = useRef<HTMLDivElement>(null)
  const [view, setView] = useState(DEFAULT_VIEW)

  useLayoutEffect(() => {
    const host = hostRef.current
    if (!host) return
    const apply = () => {
      const r = host.getBoundingClientRect()
      if (r.width >= 80 && r.height >= 80) setView({ width: r.width, height: r.height })
    }
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(apply)
    ro.observe(host)
    return () => ro.disconnect()
  }, [])

  if (overview) {
    return <DagOverview nodes={nodes} mode={mode} onSelect={onSelect} />
  }

  const keys = nodes.map((n) => n.key)
  const layout = layoutPipelineDag(keys, { mode })
  const byKey = new Map(nodes.map((n) => [n.key, n]))
  const fit = fitGraphToView(layout, view, 24, { contain })
  const patternId = `crucible-flow-cross-${gid}`
  const markerId = `crucible-flow-arrow-${gid}`

  return (
    <div
      ref={hostRef}
      className={[
        'crucible-flow-canvas',
        fit.overflowX ? 'is-overflow-x' : '',
        fit.overflowY ? 'is-overflow-y' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      role="img"
      aria-label="任务节点详细拓扑图"
      data-pipeline-mode={mode}
    >
      <svg className="crucible-flow-canvas__grid" aria-hidden>
        <defs>
          <pattern id={patternId} width="24" height="24" patternUnits="userSpaceOnUse">
            <circle className="crucible-flow-canvas__grid-dot" cx="1" cy="1" r="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${patternId})`} />
      </svg>
      <div className="crucible-flow-canvas__slot" style={{ width: fit.slotW, height: fit.slotH }}>
        <div
          className="crucible-flow-canvas__world"
          style={{
            width: layout.width,
            height: layout.height,
            transform: `translate(${fit.tx}px, ${fit.ty}px) scale(${fit.scale})`,
          }}
        >
        {layout.groups.map((group, index) => (
          <div
            key={group.key}
            className={`crucible-dag-group is-${group.tone}`}
            data-group-key={group.key}
            style={{ left: group.x, top: group.y, width: group.width, height: group.height }}
          >
            <span className="crucible-dag-group__index">{index + 1}</span>
            <span className="crucible-dag-group__copy">
              <strong>{group.label}</strong>
              <small>{group.caption}</small>
            </span>
          </div>
        ))}
        <svg className="crucible-flow-canvas__edges" width={layout.width} height={layout.height} aria-hidden>
          <defs>
            <marker
              id={markerId}
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              markerUnits="userSpaceOnUse"
              orient="auto"
            >
              <path d="M 0 1 L 8 4 L 0 7 z" className="crucible-dag__arrow" />
            </marker>
          </defs>
          {layout.edges.map((e) => {
            const showArrow = e.kind !== 'link'
            return (
              <g key={`${e.kind}:${e.from}->${e.to}`}>
                <path
                  className={[
                    'crucible-dag__edge',
                    `is-${e.kind}`,
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  d={e.d}
                  fill="none"
                  markerEnd={showArrow ? `url(#${markerId})` : undefined}
                  data-edge-from={e.from}
                  data-edge-to={e.to}
                />
                {e.label && e.labelX != null && e.labelY != null ? (
                  <text className={`crucible-dag__edge-label is-${e.kind}`} x={e.labelX} y={e.labelY}>
                    {e.label}
                  </text>
                ) : null}
              </g>
            )
          })}
        </svg>
        {layout.nodes.map((box) => {
          const model = byKey.get(box.key) ?? {
            key: box.key,
            status: 'pending',
            selectable: false,
            selected: false,
          }
          const visual = dagVisualStatus(model)
          const selectable = Boolean(onSelect && model.selectable)
          const label = NODE_LABELS[box.key] ?? box.key
          return (
            <div
              key={box.key}
              className={[
                'crucible-dag-node',
                `crucible-dag-node--${box.key}`,
                `is-${visual}`,
                `is-${box.shape}`,
                model.selected ? 'is-selected' : '',
                selectable ? 'is-selectable' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              data-node-key={box.key}
              title={model.error_message || label}
              style={{ left: box.x, top: box.y, width: box.width, height: box.height }}
              onClick={selectable ? () => onSelect?.(box.key) : undefined}
            >
              {box.shape === 'box' ? (
                <>
                  {isAiNode(box.key) ? (
                    <span className="crucible-dag-node__ai" title="AI 节点">AI</span>
                  ) : null}
                  <span className="crucible-dag-node__port is-in" />
                  <span className="crucible-dag-node__status" title={DAG_STATUS_TEXT[visual]}>{statusIcon(visual)}</span>
                  <span className="crucible-dag-node__copy">
                    <span className="crucible-dag-node__label">{label}</span>
                    <span className="crucible-dag-node__caption">{nodeCaption(box.key, model.output)}</span>
                    {model.usage && model.usage.total_tokens > 0 ? (
                      <span
                        className="crucible-dag-node__tokens"
                        title={[
                          `prompt ${model.usage.prompt_tokens}`,
                          `completion ${model.usage.completion_tokens}`,
                          `cache_read ${model.usage.cache_read_input_tokens}`,
                          `cache_creation ${model.usage.cache_creation_input_tokens}`,
                        ].join(' · ')}
                      >
                        {formatTokenCount(model.usage.total_tokens)} tok
                      </span>
                    ) : null}
                  </span>
                  <span className="crucible-dag-node__port is-out" />
                </>
              ) : box.shape === 'diamond' ? (
                <>
                  <span className="crucible-dag-node__diamond" />
                  <span className="crucible-dag-node__label">{label}</span>
                </>
              ) : (
                <span className="crucible-dag-node__label">{label}</span>
              )}
            </div>
          )
        })}
        </div>
      </div>
    </div>
  )
}
