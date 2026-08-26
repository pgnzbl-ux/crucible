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
  api_inventory: ['source', 'profile'],
  env_ready: ['source', 'profile', 'dispatch'],
  cluster: ['scan_semgrep', 'scan_gitleaks', 'scan_osv'],
  api_hunt: ['api_inventory'],
  screen: ['cluster'],
  triage: ['screen'],
  dispatch: ['triage', 'api_hunt'],
  audit: ['source', 'profile', 'dispatch'],
  reproduce: ['source', 'env_ready', 'audit'],
  report: ['profile', 'env_ready', 'audit', 'reproduce'],
}

export const SYNTHETIC_KEYS = ['lead_verify', 'over'] as const
export type SyntheticKey = (typeof SYNTHETIC_KEYS)[number]

/** discovery 中由 LeadWorker 取代执行、因而在 NodeRun 上必然 skipped 的节点。 */
export const DISCOVERY_REPLACED_NODE_KEYS: ReadonlySet<string> = new Set(['audit', 'reproduce'])

/** 仅控制展开拓扑的纵向排布；画像居中对齐 Semgrep；清单在 Semgrep 上方填满深度列。
 * 线索列把猎洞放在聚类之上，与「清单 → 猎洞」「SAST → 聚类」两条泳道对齐，避免对向斜穿。 */
const PARALLEL_STACK: readonly string[] = [
  'scan_gitleaks',
  'scan_osv',
  'profile',
  'api_inventory',
  'scan_semgrep',
  'api_hunt',
  'cluster',
  'env_ready',
]

const SIZE = {
  nodeW: 156,
  nodeH: 52,
  gapX: 64,
  gapY: 10,
  padX: 24,
  // 留给阶段卡标题条，节点紧贴标题下方，避免深度列上方大块空白
  padY: 30,
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
        caption: 'Semgrep · API 清单',
        nodeKeys: ['scan_semgrep', 'api_inventory'],
        parallel: true,
      },
      {
        key: 'clues',
        label: '线索归并',
        caption: '扫描聚类 ∥ API 猎洞直出',
        nodeKeys: ['cluster', 'api_hunt'],
        parallel: true,
      },
      {
        key: 'review',
        label: '扫描复核',
        caption: '轻量快审 · AI 二审',
        nodeKeys: ['screen', 'triage'],
      },
      { key: 'dispatch', label: '线索调度', caption: '扫描复核 ∪ 猎洞合格', nodeKeys: ['dispatch'] },
      { key: 'verify', label: '多线索终认', caption: '按需靶场 → 白盒/复现', nodeKeys: ['env_ready', 'lead_verify'] },
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
export type DagEdgeKind = 'flow' | 'conditional' | 'support' | 'link'

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
  tone: 'prepare' | 'parallel' | 'deep' | 'clues' | 'review' | 'dispatch' | 'verify' | 'result'
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
  if (key === 'scan_semgrep' || key === 'api_inventory') return 2
  // 两条线索流并列：scans→cluster ∥ inventory→hunt；复核仅扫描支路
  if (key === 'cluster' || key === 'api_hunt') return 3
  if (key === 'screen' || key === 'triage') return 4
  if (key === 'dispatch') return 5
  if (key === 'audit' || key === 'env_ready') return 6
  if (key === 'reproduce' || key === 'lead_verify') return 7
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
  add(edges, 'profile', 'api_inventory')

  add(edges, 'scan_osv', 'cluster')
  add(edges, 'scan_semgrep', 'cluster')
  add(edges, 'scan_gitleaks', 'cluster')
  add(edges, 'api_inventory', 'api_hunt')
  add(edges, 'cluster', 'screen')
  add(edges, 'screen', 'triage')
  if (vis.has('dispatch')) {
    add(edges, 'triage', 'dispatch')
    add(edges, 'api_hunt', 'dispatch')
  }

  if (mode === 'discovery') {
    if (vis.has('dispatch')) {
      add(edges, 'dispatch', 'env_ready', 'conditional', '有线索且为 Web')
      add(edges, 'env_ready', 'lead_verify', 'support', '就绪 / 降级')
      add(edges, 'lead_verify', 'report')
    } else {
      addChain(edges, vis, ['triage', 'lead_verify', 'report'])
      if (vis.has('lead_verify')) add(edges, 'api_hunt', 'lead_verify')
      else add(edges, 'api_hunt', 'report')
    }
    if (!vis.has('dispatch')) add(edges, 'env_ready', 'lead_verify', 'support', 'Web 靶场')
  } else {
    add(edges, 'profile', 'env_ready', 'conditional', '仅 Web')
    add(edges, 'profile', 'audit')
    add(edges, 'audit', 'reproduce', 'conditional', 'Gate 通过')
    add(edges, 'env_ready', 'reproduce', 'support', '动态环境')
    add(edges, 'reproduce', 'report')
  }
  add(edges, 'report', 'over')
  return edges
}

function stackKeys(keys: string[]): string[] {
  return [...keys].sort((a, b) => orderIndex(a) - orderIndex(b))
}

const SLOT_Y = SIZE.nodeH + SIZE.gapY

function columnMembers(
  stacked: Map<number, string[]>,
  byKey: Map<string, DagLayoutNode>,
  col: number,
): DagLayoutNode[] {
  return (stacked.get(col) ?? [])
    .map((key) => byKey.get(key))
    .filter((node): node is DagLayoutNode => Boolean(node))
}

function shiftNodes(nodes: readonly DagLayoutNode[], delta: number) {
  if (Math.abs(delta) < 0.5) return
  for (const node of nodes) node.y += delta
}

/** 矮列按节点数落位：两节点上空一格，单节点垂直居中；三节点列维持脊柱。 */
function placeSparseColumns(
  colIds: readonly number[],
  stacked: Map<number, string[]>,
  byKey: Map<string, DagLayoutNode>,
) {
  const tall: DagLayoutNode[] = []
  const sparse: Array<{ count: number; nodes: DagLayoutNode[] }> = []
  for (const col of colIds) {
    const nodes = columnMembers(stacked, byKey, col)
    if (nodes.length >= 3) tall.push(...nodes)
    else if (nodes.length > 0) sparse.push({ count: nodes.length, nodes })
  }
  if (!tall.length || !sparse.length) return
  const bandTop = Math.min(...tall.map((n) => n.y))
  const bandBottom = Math.max(...tall.map((n) => n.y + n.height))
  const mid = (bandTop + bandBottom) / 2
  for (const { count, nodes } of sparse) {
    if (count === 2) {
      shiftNodes(nodes, bandTop + SLOT_Y - Math.min(...nodes.map((n) => n.y)))
    } else {
      const [node] = nodes
      shiftNodes(nodes, mid - node.height / 2 - node.y)
    }
  }
}

function spineKeyInColumn(keys: string[], column: number): string | null {
  // 初筛列：画像与 Semgrep 同高；OSV 在画像上方
  if (column === 1 && keys.includes('profile')) return 'profile'
  if (column === 2 && keys.includes('scan_semgrep')) return 'scan_semgrep'
  if (column === 3 && keys.includes('cluster')) return 'cluster'
  if (column === 4 && keys.includes('screen')) return 'screen'
  return keys[Math.floor(keys.length / 2)] ?? null
}

const WIRE_HOP_R = 7

/** 竖线跨过另一条导线时画电路跳线圆弧，表示两线不相接。 */
function verticalHop(x: number, yFrom: number, yTo: number, hopY: number | null): string {
  if (hopY == null || Math.abs(yTo - yFrom) < WIRE_HOP_R * 2 + 2) return `V ${yTo}`
  const lo = Math.min(yFrom, yTo)
  const hi = Math.max(yFrom, yTo)
  if (hopY <= lo + WIRE_HOP_R || hopY >= hi - WIRE_HOP_R) return `V ${yTo}`
  // 凸向列间隙（左侧），避免圆弧贴到目标节点
  if (yTo > yFrom) {
    return `V ${hopY - WIRE_HOP_R} A ${WIRE_HOP_R} ${WIRE_HOP_R} 0 0 1 ${x} ${hopY + WIRE_HOP_R} V ${yTo}`
  }
  return `V ${hopY + WIRE_HOP_R} A ${WIRE_HOP_R} ${WIRE_HOP_R} 0 0 1 ${x} ${hopY - WIRE_HOP_R} V ${yTo}`
}

function routeBox(
  a: DagLayoutNode,
  b: DagLayoutNode,
  kind: DagEdgeKind,
  bottomRail: number,
  gitleaksCy: number | null,
  hopY: number | null,
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
  // 同列纵向：顶底中点直连
  const sameColumn = Math.abs(a.x + a.width / 2 - (b.x + b.width / 2)) < 1
  if (sameColumn && a.y + a.height <= b.y + 1) {
    const x = a.x + a.width / 2
    const y1 = a.y + a.height
    const y2 = b.y
    return {
      d: `M ${x} ${y1} V ${y2}`,
      labelX: x + 8,
      labelY: (y1 + y2) / 2,
    }
  }
  // 三扫描汇入 cluster：先汇到同一合并点，再一根进入节点
  if (b.key === 'cluster' && a.key.startsWith('scan_')) {
    return routeScanIntoCluster(a, b, gitleaksCy, hopY)
  }
  // 扫描复核与猎洞汇入 dispatch（猎洞从复核列顶上飞过，避免横穿快审/二审）
  if (b.key === 'dispatch' && (a.key === 'triage' || a.key === 'api_hunt')) {
    return routeIntoDispatch(a, b, gitleaksCy)
  }
  const x1 = a.x + a.width
  const y1 = a.y + a.height / 2
  const x2 = b.x
  const y2 = b.y + b.height / 2
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

/** 扫描列 → 聚类：共享 mergeX / clusterCy；OSV 上抬并入泄露扫描同高线段。
 * SAST 与聚类同泳道时走直线，避免和清单→猎洞对向斜穿。 */
function routeScanIntoCluster(
  a: DagLayoutNode,
  cluster: DagLayoutNode,
  gitleaksCy: number | null,
  hopY: number | null,
): { d: string; labelX: number; labelY: number } {
  const x1 = a.x + a.width
  const y1 = a.y + a.height / 2
  const x2 = cluster.x
  const cy = cluster.y + cluster.height / 2
  const mergeX = x2 - SIZE.gapX / 3
  if (Math.abs(y1 - cy) < 1) {
    return {
      d: `M ${x1} ${y1} H ${x2}`,
      labelX: (x1 + x2) / 2,
      labelY: y1 - 9,
    }
  }
  if (a.key === 'scan_osv' && gitleaksCy != null && Math.abs(y1 - gitleaksCy) > 1) {
    const entryX = x1 + SIZE.gapX / 3
    return {
      d: `M ${x1} ${y1} H ${entryX} V ${gitleaksCy} H ${mergeX} ${verticalHop(mergeX, gitleaksCy, cy, hopY)} H ${x2}`,
      labelX: (entryX + mergeX) / 2,
      labelY: gitleaksCy - 7,
    }
  }
  // gitleaks / 已与泄露同高的 OSV：沿顶轨飞过深度列，在聚类前竖落到入口；
  // 跨过清单→猎洞时用跳线圆弧，表示不相接。
  return {
    d: `M ${x1} ${y1} H ${mergeX} ${verticalHop(mergeX, y1, cy, hopY)} H ${x2}`,
    labelX: (x1 + mergeX) / 2,
    labelY: Math.min(y1, cy) - 7,
  }
}

/** 复核 / 猎洞 → 调度。猎洞走深度列空出来的顶轨飞过复核列，避免横穿快审。 */
function routeIntoDispatch(
  a: DagLayoutNode,
  dispatch: DagLayoutNode,
  gitleaksCy: number | null,
): { d: string; labelX: number; labelY: number } {
  const x1 = a.x + a.width
  const y1 = a.y + a.height / 2
  const x2 = dispatch.x
  const cy = dispatch.y + dispatch.height / 2
  const mergeX = x2 - SIZE.gapX / 3
  if (a.key === 'api_hunt') {
    const dropX = x1 + SIZE.gapX / 3
    const railY = gitleaksCy ?? SIZE.padY + SIZE.nodeH / 2
    return {
      d: `M ${x1} ${y1} H ${dropX} V ${railY} H ${mergeX} V ${cy} H ${x2}`,
      labelX: (dropX + mergeX) / 2,
      labelY: railY - 7,
    }
  }
  if (Math.abs(y1 - cy) < 1) {
    return {
      d: `M ${x1} ${y1} H ${x2}`,
      labelX: (x1 + x2) / 2,
      labelY: y1 - 9,
    }
  }
  return {
    d: `M ${x1} ${y1} H ${mergeX} V ${cy} H ${x2}`,
    labelX: (x1 + mergeX) / 2,
    labelY: Math.min(y1, cy) - 7,
  }
}

function toneForStage(key: string): DagLayoutGroup['tone'] {
  if (key === 'source') return 'prepare'
  if (key === 'initial' || key === 'profile') return 'parallel'
  if (key === 'deep' || key === 'env') return 'deep'
  if (key === 'clues') return 'clues'
  if (key === 'review') return 'review'
  if (key === 'dispatch') return 'dispatch'
  if (key === 'report') return 'result'
  return 'verify'
}

interface GroupDefinition {
  key: string
  label: string
  caption: string
  tone: DagLayoutGroup['tone']
  nodeKeys: readonly string[]
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
  const discoveryPrefix = pipelineOverviewStages('discovery').filter(
    (stage) => !['verify', 'report'].includes(stage.key),
  )
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
      y: 4,
      width: right - Math.max(10, left),
      height: maxBottom + 4,
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
  placeSparseColumns(colIds, stacked, byKey)
  const maxBottom = Math.max(...nodes.map((n) => n.y + n.height), SIZE.padY)
  const supportRail = maxBottom + 18
  const height = supportRail + 22
  const gitleaks = byKey.get('scan_gitleaks')
  const gitleaksCy = gitleaks ? gitleaks.y + gitleaks.height / 2 : null
  const hopLane = byKey.get('api_hunt') ?? byKey.get('api_inventory')
  const hopY = hopLane ? hopLane.y + hopLane.height / 2 : null

  const edges: DagLayoutEdge[] = flowEdges(keys, mode).flatMap((e) => {
    const a = byKey.get(e.from)
    const b = byKey.get(e.to)
    if (!a || !b) return []
    const route = routeBox(a, b, e.kind, supportRail, gitleaksCy, hopY)
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
