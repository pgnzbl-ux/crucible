/** 运行流程图，与 backend `DEFAULT_PIPELINE` 及 LeadWorker 语义对齐。
 *
 * discovery 不展示必然 skipped 的 DAG audit/reproduce 占位，改由合成
 * `lead_verify` 表示真正执行的多 LeadRun 终认。verify 则展示单漏洞节点链。
 */

import { PIPELINE_NODE_ORDER } from './meta'

export type PipelineMode = 'verify' | 'discovery'

/** 与 backend `DEFAULT_PIPELINE.requires` 逐项对齐。 */
export const PIPELINE_REQUIRES: Record<string, readonly string[]> = {
  source: [],
  profile: ['source'],
  scan_gitleaks: ['source'],
  scan_osv: ['source'],
  scan_semgrep: ['source', 'profile'],
  env_ready: ['source', 'profile'],
  cluster: ['scan_semgrep', 'scan_gitleaks', 'scan_osv'],
  screen: ['cluster'],
  triage: ['screen'],
  dispatch: ['triage'],
  audit: ['source', 'profile', 'dispatch'],
  reproduce: ['source', 'env_ready', 'audit'],
  report: ['profile', 'env_ready', 'audit', 'reproduce'],
}

export const SYNTHETIC_KEYS = ['lead_verify', 'over'] as const
export type SyntheticKey = (typeof SYNTHETIC_KEYS)[number]

/** discovery 中由 LeadWorker 取代执行、因而在 NodeRun 上必然 skipped 的节点。 */
export const DISCOVERY_REPLACED_NODE_KEYS: ReadonlySet<string> = new Set(['audit', 'reproduce'])

/** 仅控制展开拓扑的纵向排布；画像居中对齐 Semgrep，泄露扫描走上方跨列导轨。 */
const PARALLEL_STACK: readonly string[] = [
  'scan_gitleaks',
  'profile',
  'scan_osv',
  'scan_semgrep',
  'env_ready',
]

const SIZE = {
  nodeW: 156,
  nodeH: 56,
  gapX: 72,
  gapY: 16,
  padX: 34,
  padY: 56,
  diamond: 76,
  terminal: 44,
}

export type DagVisualStatus =
  | 'pending'
  | 'blocked'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped'
  | 'cancelled'
  | 'degraded'

export const DAG_STATUS_TEXT: Record<DagVisualStatus, string> = {
  pending: '等待',
  blocked: '未执行',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
  cancelled: '已取消',
  degraded: '部分降级',
}

export interface PipelineOverviewStage {
  key: string
  label: string
  caption: string
  nodeKeys: readonly string[]
  parallel?: boolean
}

/** 顶部总览和纵向进度共用同一份业务阶段，避免两套 UI 对流程各说各话。 */
export function pipelineOverviewStages(mode: PipelineMode): PipelineOverviewStage[] {
  if (mode === 'discovery') {
    return [
      { key: 'source', label: '准备源码', caption: '获取仓库', nodeKeys: ['source'] },
      {
        key: 'initial',
        label: '并行初筛',
        caption: '画像 + Gitleaks + OSV',
        nodeKeys: ['profile', 'scan_gitleaks', 'scan_osv'],
        parallel: true,
      },
      {
        key: 'deep',
        label: '深度分析',
        caption: 'Semgrep + Web 靶场',
        nodeKeys: ['scan_semgrep', 'env_ready'],
        parallel: true,
      },
      { key: 'review', label: '发现复核', caption: '归并 · 轻量快审 · AI 二审', nodeKeys: ['cluster', 'screen', 'triage'] },
      { key: 'dispatch', label: '线索调度', caption: '进入终认队列', nodeKeys: ['dispatch'] },
      { key: 'verify', label: '多线索终认', caption: '白盒 + 可选复现', nodeKeys: ['lead_verify'] },
      { key: 'report', label: '审计报告', caption: '聚合最终结果', nodeKeys: ['report'] },
    ]
  }
  return [
    { key: 'source', label: '准备源码', caption: '获取仓库', nodeKeys: ['source'] },
    { key: 'profile', label: '项目画像', caption: '识别技术栈', nodeKeys: ['profile'] },
    { key: 'env', label: '运行环境', caption: '非 Web 自动跳过', nodeKeys: ['env_ready'] },
    { key: 'audit', label: '白盒审计', caption: '确认代码路径', nodeKeys: ['audit'] },
    { key: 'reproduce', label: '动态复现', caption: 'Gate 未通过则跳过', nodeKeys: ['reproduce'] },
    { key: 'report', label: '验证报告', caption: '输出最终结论', nodeKeys: ['report'] },
  ]
}

export type DagShape = 'box' | 'diamond' | 'terminal'
export type DagEdgeKind = 'flow' | 'conditional' | 'support'

export interface DagLayoutNode {
  key: string
  layer: number
  indexInLayer: number
  x: number
  y: number
  width: number
  height: number
  shape: DagShape
}

export interface DagLayoutEdge {
  from: string
  to: string
  d: string
  kind: DagEdgeKind
  label?: string
  labelX?: number
  labelY?: number
}

export interface DagLayoutGroup {
  key: string
  label: string
  caption: string
  tone: 'prepare' | 'parallel' | 'deep' | 'review' | 'dispatch' | 'verify' | 'result'
  x: number
  y: number
  width: number
  height: number
}

export interface DagLayout {
  width: number
  height: number
  nodes: DagLayoutNode[]
  edges: DagLayoutEdge[]
  groups: DagLayoutGroup[]
}

export interface DagLayoutOptions {
  mode?: PipelineMode
}

function orderIndex(key: string): number {
  const parallel = PARALLEL_STACK.indexOf(key)
  if (parallel >= 0) return 20 + parallel
  const i = PIPELINE_NODE_ORDER.indexOf(key)
  if (i >= 0) return i
  return key === 'lead_verify' ? 90 : 999
}

/** 就绪波列号；缺席的列会在布局时自动压缩。 */
export function flowColumn(key: string, _mode: PipelineMode): number {
  if (key === 'source') return 0
  if (key === 'profile' || key === 'scan_gitleaks' || key === 'scan_osv') return 1
  if (key === 'scan_semgrep' || key === 'env_ready') return 2
  if (key === 'cluster') return 3
  if (key === 'screen' || key === 'triage') return 4
  if (key === 'dispatch') return 5
  if (key === 'audit' || key === 'lead_verify') return 6
  if (key === 'reproduce') return 7
  if (key === 'report') return 8
  if (key === 'over') return 9
  return 1
}

function shapeOf(key: string): DagShape {
  if (key === 'over') return 'terminal'
  return 'box'
}

function boxSize(shape: DagShape): { width: number; height: number } {
  if (shape === 'diamond') return { width: SIZE.diamond, height: SIZE.diamond }
  if (shape === 'terminal') return { width: SIZE.terminal, height: SIZE.terminal }
  return { width: SIZE.nodeW, height: SIZE.nodeH }
}

function withSynthetics(keys: readonly string[], mode: PipelineMode): string[] {
  const out = keys.filter((key) => !SYNTHETIC_KEYS.includes(key as SyntheticKey))
  if (mode === 'discovery') {
    for (let i = out.length - 1; i >= 0; i -= 1) {
      if (DISCOVERY_REPLACED_NODE_KEYS.has(out[i])) out.splice(i, 1)
    }
    if (out.includes('dispatch') && out.includes('report')) {
      out.splice(out.indexOf('report'), 0, 'lead_verify')
    }
  }
  out.push('over')
  return out
}

/** 与当前实际执行波一致的流程边；省略可由上游传递保证的冗余 requires 边。 */
export function flowEdges(
  visibleKeys: readonly string[],
  mode: PipelineMode,
): Array<{ from: string; to: string; kind: DagEdgeKind; label?: string }> {
  const vis = new Set(visibleKeys)
  const add = (
    acc: Array<{ from: string; to: string; kind: DagEdgeKind; label?: string }>,
    from: string,
    to: string,
    kind: DagEdgeKind = 'flow',
    label?: string,
  ) => {
    if (vis.has(from) && vis.has(to)) acc.push({ from, to, kind, label })
  }
  const addChain = (
    acc: Array<{ from: string; to: string; kind: DagEdgeKind; label?: string }>,
    visible: ReadonlySet<string>,
    chain: string[],
  ) => {
    const present = chain.filter((k) => visible.has(k))
    for (let i = 0; i < present.length - 1; i++) add(acc, present[i], present[i + 1])
  }
  const edges: Array<{ from: string; to: string; kind: DagEdgeKind; label?: string }> = []
  add(edges, 'source', 'profile')
  add(edges, 'source', 'scan_gitleaks')
  add(edges, 'source', 'scan_osv')
  add(edges, 'profile', 'scan_semgrep')
  add(edges, 'profile', 'env_ready', 'conditional', '仅 Web')

  add(edges, 'scan_osv', 'cluster')
  add(edges, 'scan_semgrep', 'cluster')
  add(edges, 'scan_gitleaks', 'cluster')

  if (mode === 'discovery') {
    addChain(edges, vis, ['cluster', 'screen', 'triage', 'dispatch', 'lead_verify', 'report'])
    add(edges, 'env_ready', 'lead_verify', 'support', 'Web 靶场')
  } else {
    if (vis.has('dispatch')) {
      addChain(edges, vis, ['cluster', 'screen', 'triage', 'dispatch', 'audit'])
      // 当前波次调度会等 env_ready 所在波收敛后才处理 skip 链并进入 audit。
      add(edges, 'env_ready', 'audit', 'support', '环境分支收敛')
    } else if (vis.has('env_ready')) {
      add(edges, 'env_ready', 'audit', 'support', '就绪 / 跳过')
    } else {
      add(edges, 'profile', 'audit')
    }
    add(edges, 'audit', 'reproduce', 'conditional', 'Gate 通过')
    add(edges, 'reproduce', 'report')
  }
  add(edges, 'report', 'over')
  return edges
}

function stackKeys(keys: string[]): string[] {
  return [...keys].sort((a, b) => orderIndex(a) - orderIndex(b))
}

function spineKeyInColumn(keys: string[], column: number): string | null {
  if (column === 2 && keys.includes('scan_semgrep')) return 'scan_semgrep'
  return keys[Math.floor(keys.length / 2)] ?? null
}

function routeBox(
  a: DagLayoutNode,
  b: DagLayoutNode,
  kind: DagEdgeKind,
  bottomRail: number,
  topFlowRail: number,
  bottomFlowRail: number,
): { d: string; labelX: number; labelY: number } {
  if (kind === 'support') {
    const x1 = a.x + a.width / 2
    const y1 = a.y + a.height
    const x2 = b.x + b.width / 2
    const y2 = b.y + b.height
    return {
      d: `M ${x1} ${y1} V ${bottomRail} H ${x2} V ${y2}`,
      labelX: (x1 + x2) / 2,
      labelY: bottomRail - 7,
    }
  }
  const x1 = a.x + a.width
  const y1 = a.y + a.height / 2
  const x2 = b.x
  const y2 = b.y + b.height / 2
  // gitleaks / osv 可早于深度分析完成，汇入 cluster 时跨过一个可见列。
  if (x2 - x1 > SIZE.gapX + SIZE.nodeW / 2) {
    const entryX = x1 + SIZE.gapX / 3
    const exitX = x2 - SIZE.gapX / 3
    // Gitleaks 已放在最上方：直接穿过上方空白，只在 Cluster 前下折一次。
    if (a.key === 'scan_gitleaks') {
      return {
        d: `M ${x1} ${y1} H ${exitX} V ${y2} H ${x2}`,
        labelX: (x1 + exitX) / 2,
        labelY: y1 - 7,
      }
    }
    // OSV 仍从节点区下方绕行，避免穿过 Semgrep 或靶场卡片。
    const railY = a.key === 'scan_osv' ? bottomFlowRail : topFlowRail
    return {
      d: `M ${x1} ${y1} H ${entryX} V ${railY} H ${exitX} V ${y2} H ${x2}`,
      labelX: (entryX + exitX) / 2,
      labelY: railY - 7,
    }
  }
  if (Math.abs(y1 - y2) < 1) {
    return {
      d: `M ${x1} ${y1} H ${x2}`,
      labelX: (x1 + x2) / 2,
      labelY: y1 - 9,
    }
  }
  // 一对多 / 多对一：竖段走两列中间的汇流槽，不要贴在目标左缘
  const busX = (x1 + x2) / 2
  return {
    d: `M ${x1} ${y1} H ${busX} V ${y2} H ${x2}`,
    labelX: busX + 7,
    labelY: (y1 + y2) / 2 - 5,
  }
}

type GroupDefinition = Pick<DagLayoutGroup, 'key' | 'label' | 'caption' | 'tone'> & {
  nodeKeys: readonly string[]
}

function toneForStage(key: string): DagLayoutGroup['tone'] {
  if (key === 'source') return 'prepare'
  if (key === 'initial' || key === 'profile') return 'parallel'
  if (key === 'deep' || key === 'env') return 'deep'
  if (key === 'review') return 'review'
  if (key === 'dispatch') return 'dispatch'
  if (key === 'report') return 'result'
  return 'verify'
}

function toGroupDefinition(stage: PipelineOverviewStage): GroupDefinition {
  return {
    key: stage.key,
    label: stage.label,
    caption: stage.caption,
    tone: toneForStage(stage.key),
    nodeKeys: stage.key === 'report' ? [...stage.nodeKeys, 'over'] : stage.nodeKeys,
  }
}

function groupDefinitions(mode: PipelineMode, visible: ReadonlySet<string>): GroupDefinition[] {
  if (mode === 'discovery') {
    return pipelineOverviewStages('discovery').map(toGroupDefinition)
  }
  const showsDiscovery = ['scan_gitleaks', 'cluster', 'dispatch'].some((key) => visible.has(key))
  if (!showsDiscovery) return pipelineOverviewStages('verify').map(toGroupDefinition)

  // 用户显式打开 verify 中被跳过的发现节点时，按 discovery 的真实阶段补充展示；
  // audit/reproduce/report 仍沿用 verify 的阶段定义。
  const discoveryPrefix = pipelineOverviewStages('discovery').slice(0, 5)
  const verifyTail = pipelineOverviewStages('verify').filter((stage) =>
    ['audit', 'reproduce', 'report'].includes(stage.key),
  )
  return [...discoveryPrefix, ...verifyTail].map(toGroupDefinition)
}

function layoutGroups(
  nodes: readonly DagLayoutNode[],
  mode: PipelineMode,
  maxBottom: number,
): DagLayoutGroup[] {
  const byKey = new Map(nodes.map((node) => [node.key, node]))
  const visible = new Set(byKey.keys())
  return groupDefinitions(mode, visible).flatMap((definition) => {
    const members = definition.nodeKeys.flatMap((key) => {
      const node = byKey.get(key)
      return node ? [node] : []
    })
    if (!members.length) return []
    const left = Math.min(...members.map((node) => node.x)) - 16
    const right = Math.max(...members.map((node) => node.x + node.width)) + 16
    return [{
      key: definition.key,
      label: definition.label,
      caption: definition.caption,
      tone: definition.tone,
      x: Math.max(10, left),
      y: 12,
      width: right - Math.max(10, left),
      height: maxBottom + 14,
    }]
  })
}

export function layoutPipelineDag(
  visibleKeys: readonly string[],
  opts: DagLayoutOptions = {},
): DagLayout {
  const mode: PipelineMode = opts.mode ?? 'discovery'
  const keys = withSynthetics(visibleKeys, mode)
  const grouped = new Map<number, string[]>()
  for (const key of keys) {
    const col = flowColumn(key, mode)
    const row = grouped.get(col) ?? []
    row.push(key)
    grouped.set(col, row)
  }
  const colIds = [...grouped.keys()].sort((a, b) => a - b)
  const stacked = new Map<number, string[]>()
  for (const col of colIds) {
    stacked.set(col, stackKeys(grouped.get(col) ?? []))
  }

  const colIndex = new Map<number, number>()
  colIds.forEach((id, i) => colIndex.set(id, i))

  const maxStack = Math.max(1, ...[...stacked.values()].map((c) => c.length))
  const stackH = maxStack * SIZE.nodeH + Math.max(0, maxStack - 1) * SIZE.gapY
  const spineY = SIZE.padY + stackH / 2

  const nodes: DagLayoutNode[] = []
  const byKey = new Map<string, DagLayoutNode>()
  for (const col of colIds) {
    const colKeys = stacked.get(col) ?? []
    const spineKey = spineKeyInColumn(colKeys, col)
    const spineIdx = Math.max(0, colKeys.indexOf(spineKey ?? colKeys[0] ?? ''))
    colKeys.forEach((key, indexInLayer) => {
      const shape = shapeOf(key)
      const { width, height: h } = boxSize(shape)
      const cx = SIZE.padX + (colIndex.get(col) ?? 0) * (SIZE.nodeW + SIZE.gapX) + SIZE.nodeW / 2
      const cy = spineY + (indexInLayer - spineIdx) * (SIZE.nodeH + SIZE.gapY)
      const node: DagLayoutNode = {
        key,
        layer: col,
        indexInLayer,
        x: cx - width / 2,
        y: cy - h / 2,
        width,
        height: h,
        shape,
      }
      nodes.push(node)
      byKey.set(key, node)
    })
  }
  const minY = Math.min(...nodes.map((n) => n.y), SIZE.padY)
  const shift = SIZE.padY - minY
  if (shift > 0) {
    for (const n of nodes) n.y += shift
  }
  const maxBottom = Math.max(...nodes.map((n) => n.y + n.height), SIZE.padY)
  const minTop = Math.min(...nodes.map((n) => n.y))
  const topFlowRail = Math.max(42, minTop - 12)
  const bottomFlowRail = maxBottom + 10
  const supportRail = maxBottom + 28
  const height = supportRail + 34

  const edges: DagLayoutEdge[] = flowEdges(keys, mode).flatMap((e) => {
    const a = byKey.get(e.from)
    const b = byKey.get(e.to)
    if (!a || !b) return []
    const route = routeBox(a, b, e.kind, supportRail, topFlowRail, bottomFlowRail)
    return [{
      from: e.from,
      to: e.to,
      kind: e.kind,
      label: e.label,
      d: route.d,
      labelX: route.labelX,
      labelY: route.labelY,
    }]
  })

  const maxX = Math.max(0, ...nodes.map((n) => n.x + n.width))
  return {
    width: maxX + SIZE.padX,
    height,
    nodes,
    edges,
    groups: layoutGroups(nodes, mode, maxBottom),
  }
}

export interface FitView {
  scale: number
  tx: number
  ty: number
  overflowX: boolean
  overflowY: boolean
  slotW: number
  slotH: number
}

export function fitGraphToView(
  graph: { width: number; height: number },
  view: { width: number; height: number },
  padding = 24,
  opts: { contain?: boolean } = {},
): FitView {
  const contentW = Math.max(graph.width, 1)
  const contentH = Math.max(graph.height, 1)
  const availW = Math.max(1, view.width - padding * 2)
  const availH = Math.max(1, view.height - 16)
  if (opts.contain) {
    const scale = Math.min(Math.max(0.42, Math.min(availW / contentW, availH / contentH)), 1.1)
    const w = contentW * scale
    const h = contentH * scale
    return {
      scale,
      tx: Math.max(0, (view.width - w) / 2),
      ty: Math.max(0, (view.height - h) / 2),
      overflowX: false,
      overflowY: false,
      slotW: Math.max(view.width, 1),
      slotH: Math.max(view.height, 1),
    }
  }
  const grow = Math.min(availW / contentW, availH / contentH, 1.25)
  const scale = Math.max(1, grow)
  const w = contentW * scale
  const h = contentH * scale
  const overflowX = w + padding * 2 > view.width + 0.5
  const overflowY = h + 16 > view.height + 0.5
  const tx = overflowX ? padding : Math.max(0, (view.width - w) / 2)
  const ty = overflowY ? 8 : Math.max(0, (view.height - h) / 2)
  return {
    scale,
    tx,
    ty,
    overflowX,
    overflowY,
    slotW: overflowX ? Math.ceil(tx + w + padding) : Math.max(view.width, 1),
    slotH: overflowY ? Math.ceil(ty + h + 8) : Math.max(view.height, 1),
  }
}

export function dagVisualStatus(input: {
  status: string
  error_message?: string | null
  output?: Record<string, unknown> | null
}): DagVisualStatus {
  const { status, error_message, output } = input
  if (status === 'failed' || status === 'cancelled' || status === 'skipped' || status === 'running' || status === 'pending' || status === 'blocked') {
    return status
  }
  if (status === 'completed') {
    const engineFailed = output?.status === 'failed' || Boolean(error_message)
    return engineFailed ? 'degraded' : 'completed'
  }
  return 'pending'
}
