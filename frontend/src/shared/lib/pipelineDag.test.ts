import { describe, expect, it } from 'vitest'

import { PIPELINE_NODE_ORDER, VERIFY_MODE_SKIPPED_KEYS } from './meta'
import {
  dagVisualStatus,
  fitGraphToView,
  flowColumn,
  flowEdges,
  layoutPipelineDag,
  pipelineOverviewStages,
  PIPELINE_REQUIRES,
} from './pipelineDag'

describe('pipelineDag 前提表契约', () => {
  it('every pipeline node has requires declared', () => {
    for (const key of PIPELINE_NODE_ORDER) {
      expect(PIPELINE_REQUIRES[key], key).toBeDefined()
    }
    expect(Object.keys(PIPELINE_REQUIRES)).toEqual(PIPELINE_NODE_ORDER)
  })

  it('PIPELINE_NODE_ORDER 是合法执行序：每个节点排在其全部 requires 之后', () => {
    for (const [key, requires] of Object.entries(PIPELINE_REQUIRES)) {
      const at = PIPELINE_NODE_ORDER.indexOf(key)
      for (const dep of requires) {
        expect(at, `${key} 应排在依赖 ${dep} 之后`).toBeGreaterThan(PIPELINE_NODE_ORDER.indexOf(dep))
      }
    }
  })

  it('keeps the backend audit dependency even when verify hides skipped discovery nodes', () => {
    expect(PIPELINE_REQUIRES.audit).toEqual(['source', 'profile', 'dispatch'])
    expect(PIPELINE_REQUIRES.env_ready).toEqual(['source', 'profile', 'dispatch'])
  })

  it('routes hunt candidates through the unified review chain', () => {
    expect(PIPELINE_REQUIRES.api_hunt).toEqual(['api_inventory'])
    expect(PIPELINE_REQUIRES.cluster).toEqual([
      'scan_semgrep', 'scan_gitleaks', 'scan_osv', 'api_hunt',
    ])
    expect(PIPELINE_REQUIRES.screen).toEqual(['cluster'])
    expect(PIPELINE_REQUIRES.dispatch).toEqual(['triage'])
  })

    it('makes lead_verify then finalize explicit DAG nodes before report', () => {
    expect(PIPELINE_REQUIRES.lead_verify).toEqual(['dispatch', 'env_ready'])
    expect(PIPELINE_REQUIRES.finalize).toEqual([
      'profile', 'env_ready', 'lead_verify',
    ])
    expect(PIPELINE_REQUIRES.report).toEqual(['finalize'])
  })
})

describe('pipelineDag 运行流程', () => {
  it('uses the backend ready waves in the shared discovery stage model', () => {
    const stages = pipelineOverviewStages('discovery')
    expect(stages[1].nodeKeys).toEqual(['profile', 'scan_gitleaks', 'scan_osv'])
    expect(stages[1].parallel).toBe(true)
    expect(stages[2].nodeKeys).toEqual(['scan_semgrep', 'api_inventory', 'api_hunt'])
    expect(stages[2].parallel).toBe(true)
    expect(stages.find((stage) => stage.key === 'clues')?.nodeKeys).toEqual(['cluster'])
    expect(stages.find((stage) => stage.key === 'review')?.nodeKeys).toEqual([
      'screen', 'triage',
    ])
    expect(stages.at(-3)?.nodeKeys).toEqual(['env_ready', 'lead_verify'])
    expect(stages.at(-2)?.nodeKeys).toEqual(['finalize'])
    expect(stages.at(-1)?.nodeKeys).toEqual(['report'])
  })

  it('discovery: keeps dependency lanes readable before lead verification', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const x = (k: string) => layout.nodes.find((n) => n.key === k)!.x
    const y = (k: string) => layout.nodes.find((n) => n.key === k)!.y
    const node = (k: string) => layout.nodes.find((n) => n.key === k)!

    expect(x('source')).toBeLessThan(x('profile'))
    expect(x('profile')).toBeLessThan(x('scan_semgrep'))
    expect(x('scan_semgrep')).toBeLessThan(x('api_hunt'))
    expect(x('api_hunt')).toBeLessThan(x('cluster'))
    expect(x('scan_semgrep')).toBeLessThan(x('cluster'))
    expect(x('cluster')).toBeLessThan(x('screen'))
    expect(x('screen')).toBeCloseTo(x('triage'), 0)
    expect(x('triage')).toBeLessThan(x('dispatch'))
    expect(x('dispatch')).toBeLessThan(x('lead_verify'))
    expect(x('lead_verify')).toBeLessThan(x('finalize'))
    expect(x('finalize')).toBeLessThan(x('report'))

    expect(node('scan_osv').x).toBeCloseTo(node('scan_gitleaks').x, 0)
    expect(node('profile').x).toBeCloseTo(node('scan_osv').x, 0)
    expect(node('profile').x).toBeLessThan(node('scan_semgrep').x)
    expect(node('scan_semgrep').x).toBeLessThan(node('env_ready').x)
    expect(node('env_ready').x).toBeLessThan(node('lead_verify').x)
    expect(y('scan_gitleaks')).toBeLessThan(y('scan_osv'))
    expect(y('scan_osv')).toBeLessThan(y('profile'))
    expect(y('profile')).toBeCloseTo(y('scan_semgrep'), 0)
    expect(y('scan_osv')).toBeCloseTo(y('api_inventory'), 0)
    expect(y('api_inventory')).toBeLessThan(y('scan_semgrep'))

    expect(layout.nodes.some((n) => n.key === 'is_web')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'audit')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'reproduce')).toBe(false)
    expect(node('over').shape).toBe('terminal')
    expect(Math.min(...layout.nodes.map((n) => n.y))).toBeGreaterThanOrEqual(0)
    expect(flowColumn('profile', 'discovery')).toBe(flowColumn('scan_gitleaks', 'discovery'))
    expect(flowColumn('scan_gitleaks', 'discovery')).toBeLessThan(flowColumn('scan_semgrep', 'discovery'))
    expect(flowColumn('scan_semgrep', 'discovery')).toBeLessThan(flowColumn('env_ready', 'discovery'))
    expect(layout.groups.map((group) => group.key)).toEqual([
      'source',
      'initial',
      'deep',
      'clues',
      'review',
      'dispatch',
      'verify',
      'finalize',
      'report',
    ])
    expect(layout.groups.map(({ key, label, caption }) => ({ key, label, caption }))).toEqual(
      pipelineOverviewStages('discovery').map(({ key, label, caption }) => ({ key, label, caption })),
    )
  })

  it('unifies four finding streams before review', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const node = (k: string) => layout.nodes.find((n) => n.key === k)!

    expect(node('api_hunt').x).toBeLessThan(node('cluster').x)
    expect(node('screen').x).toBeCloseTo(node('triage').x, 0)
    expect(node('screen').y).toBeLessThan(node('triage').y)
    expect(flowColumn('api_hunt', 'discovery')).toBeLessThan(flowColumn('cluster', 'discovery'))
    expect(flowColumn('screen', 'discovery')).toBe(flowColumn('triage', 'discovery'))
    expect(flowColumn('cluster', 'discovery')).toBeLessThan(flowColumn('screen', 'discovery'))
    expect(flowColumn('triage', 'discovery')).toBeLessThan(flowColumn('dispatch', 'discovery'))

    const screenToTriage = layout.edges.find((e) => e.from === 'screen' && e.to === 'triage')!
    expect(screenToTriage.kind).toBe('flow')
    expect(screenToTriage.d).toMatch(/^M [\d.]+ [\d.]+ V [\d.]+$/)

    expect(layout.edges.some((e) => e.from === 'api_hunt' && e.to === 'screen')).toBe(false)
    expect(layout.edges).toContainEqual(expect.objectContaining({ from: 'api_hunt', to: 'cluster' }))
    expect(layout.edges.some((e) => e.from === 'api_hunt' && e.to === 'dispatch')).toBe(false)
    expect(layout.edges).toContainEqual(expect.objectContaining({ from: 'triage', to: 'dispatch' }))
    expect(layout.edges).toContainEqual(expect.objectContaining({ from: 'cluster', to: 'screen' }))

    expect(layout.groups.some((g) => g.key === 'review')).toBe(true)
    expect(layout.groups.some((g) => g.key === 'deep')).toBe(true)
  })

  it('keeps deep→clue lanes parallel instead of crossing', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const node = (k: string) => layout.nodes.find((n) => n.key === k)!
    const y = (k: string) => node(k).y
    const edge = (from: string, to: string) =>
      layout.edges.find((item) => item.from === from && item.to === to)!

    expect(y('api_hunt')).toBeCloseTo(y('api_inventory'), 0)
    expect(node('api_hunt').x).toBeLessThan(node('cluster').x)

    const huntLane = edge('api_inventory', 'api_hunt')
    const clusterLane = edge('api_hunt', 'cluster')
    expect(huntLane.d).toMatch(/^M [\d.]+ [\d.]+ H [\d.]+$/)
    expect(clusterLane.d).not.toMatch(/ V /)
    expect(edge('scan_gitleaks', 'cluster').d).toMatch(new RegExp(`H ${node('cluster').x}$`))
  })

  it('connects every finding source to the unified cluster', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    for (const from of ['scan_gitleaks', 'scan_osv', 'scan_semgrep', 'api_hunt']) {
      expect(layout.edges).toContainEqual(expect.objectContaining({ from, to: 'cluster' }))
    }
  })

  it('leaves one vacant slot above two-node stages and centers single-node stages', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const node = (k: string) => layout.nodes.find((n) => n.key === k)!
    const y = (k: string) => node(k).y

    expect(y('api_hunt')).toBeCloseTo(y('api_inventory'), 0)
    expect(y('cluster')).toBeCloseTo(y('api_hunt'), 0)
    expect(y('screen')).toBeCloseTo(y('api_hunt'), 0)
    expect(y('triage')).toBeCloseTo(y('scan_semgrep'), 0)
    expect(y('api_hunt')).toBeGreaterThan(y('scan_gitleaks'))
    expect(node('env_ready').x).toBeLessThan(node('lead_verify').x)

    const bandTop = y('scan_gitleaks')
    const bandBottom = node('scan_semgrep').y + node('scan_semgrep').height
    expect(node('source').y + node('source').height / 2).toBeCloseTo((bandTop + bandBottom) / 2, 0)
    expect(y('dispatch')).toBeCloseTo(y('source'), 0)
    expect(y('report')).toBeCloseTo(y('source'), 0)

    expect(y('profile')).toBeCloseTo(y('scan_semgrep'), 0)
    expect(y('scan_osv')).toBeCloseTo(y('api_inventory'), 0)
  })

  it('routes scan_osv into the unified cluster without reversing direction', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const cluster = layout.nodes.find((n) => n.key === 'cluster')!
    const osvEdge = layout.edges.find((e) => e.from === 'scan_osv' && e.to === 'cluster')!
    expect(osvEdge.d).toMatch(new RegExp(`H ${cluster.x}$`))
    expect(osvEdge.d).not.toMatch(new RegExp(`H ${cluster.x} V`))
  })

  it('merges four finding edges into cluster', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const cluster = layout.nodes.find((n) => n.key === 'cluster')!
    const intoCluster = (from: string) =>
      layout.edges.find((e) => e.from === from && e.to === 'cluster')!

    for (const from of ['scan_gitleaks', 'scan_osv', 'scan_semgrep', 'api_hunt']) {
      expect(intoCluster(from).d).toMatch(new RegExp(`H ${cluster.x}$`))
    }
  })

  it('discovery edges match runtime waves and LeadWorker handoff', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const edges = flowEdges(layout.nodes.map((n) => n.key), 'discovery')
    expect(edges).toContainEqual(expect.objectContaining({ from: 'source', to: 'scan_osv' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'source', to: 'scan_gitleaks' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'profile', to: 'scan_semgrep' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'scan_gitleaks', to: 'cluster' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'api_inventory', to: 'api_hunt' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'api_hunt', to: 'cluster' }))
    expect(edges.some((edge) => edge.from === 'api_hunt' && edge.to === 'dispatch')).toBe(false)
    expect(edges).toContainEqual(expect.objectContaining({ from: 'triage', to: 'dispatch' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'env_ready', to: 'lead_verify' }))
    expect(edges.some((edge) => edge.from === 'dispatch' && edge.to === 'lead_verify')).toBe(false)
    expect(edges).toContainEqual(expect.objectContaining({ from: 'lead_verify', to: 'finalize' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'finalize', to: 'report' }))
    expect(edges.some((e) => e.from === 'api_hunt' && e.to === 'screen')).toBe(false)
    expect(edges.some((e) => e.from === 'is_web' || e.to === 'is_web')).toBe(false)
    expect(edges.some((e) => e.from === 'audit' || e.to === 'audit')).toBe(false)
    expect(edges).toContainEqual(expect.objectContaining({
      from: 'dispatch',
      to: 'env_ready',
      kind: 'conditional',
      label: '有线索且为 Web',
    }))
    expect(edges).toContainEqual(expect.objectContaining({
      from: 'env_ready',
      to: 'lead_verify',
      kind: 'support',
    }))
  })

  it('keeps the backbone connected when dispatch is not on the canvas yet', () => {
    const order = PIPELINE_NODE_ORDER.filter((k) => k !== 'dispatch')
    const layout = layoutPipelineDag(order, { mode: 'discovery' })
    const edges = flowEdges(layout.nodes.map((n) => n.key), 'discovery')
    // 无 dispatch 时仍保留真实 lead_verify/finalize；复核经终认接到报告
    expect(edges).toContainEqual(expect.objectContaining({ from: 'triage', to: 'lead_verify' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'lead_verify', to: 'finalize' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'finalize', to: 'report' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'api_hunt', to: 'cluster' }))
    expect(edges.some((e) => e.from === 'dispatch' || e.to === 'dispatch')).toBe(false)
  })

  it('verify: hidden discovery nodes follow the current sequential wave order', () => {
    const visible = PIPELINE_NODE_ORDER.filter((k) => !VERIFY_MODE_SKIPPED_KEYS.has(k))
    const layout = layoutPipelineDag(visible, { mode: 'verify' })
    const node = (k: string) => layout.nodes.find((n) => n.key === k)!

    expect(layout.nodes.some((n) => n.key === 'cluster')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'dispatch')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'api_inventory')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'api_hunt')).toBe(false)
    expect(node('env_ready').x).toBeCloseTo(node('audit').x, 0)
    expect(node('audit').x).toBeLessThan(node('reproduce').x)
    expect(layout.nodes.some((n) => n.key === 'is_web')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'lead_verify')).toBe(false)

    const edges = flowEdges(
      layout.nodes.map((n) => n.key),
      'verify',
    )
    expect(edges).toContainEqual(expect.objectContaining({ from: 'profile', to: 'env_ready' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'profile', to: 'audit' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'env_ready', to: 'reproduce' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'audit', to: 'reproduce' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'reproduce', to: 'finalize' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'finalize', to: 'report' }))
    expect(edges.some((e) => e.from === 'dispatch')).toBe(false)
  })

  it('verify expanded diagnostics keeps audit independent from the discovery chain', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'verify' })
    expect(layout.nodes.some((n) => n.key === 'dispatch')).toBe(true)
    expect(layout.nodes.some((n) => n.key === 'lead_verify')).toBe(false)
    expect(layout.edges).toContainEqual(expect.objectContaining({ from: 'profile', to: 'audit' }))
    expect(layout.edges).toContainEqual(expect.objectContaining({
      from: 'env_ready',
      to: 'reproduce',
      kind: 'support',
    }))
    expect(layout.edges.some((edge) => edge.from === 'dispatch' && edge.to === 'audit')).toBe(false)
    expect(layout.groups.map((group) => group.key)).toEqual([
      'source',
      'initial',
      'deep',
      'clues',
      'review',
      'dispatch',
      'audit',
      'reproduce',
      'finalize',
      'report',
    ])
  })

  it('verify collapsed and expanded-default use identical business categories', () => {
    const visible = PIPELINE_NODE_ORDER.filter((key) => !VERIFY_MODE_SKIPPED_KEYS.has(key))
    const layout = layoutPipelineDag(visible, { mode: 'verify' })
    expect(layout.groups.map(({ key, label, caption }) => ({ key, label, caption }))).toEqual(
      pipelineOverviewStages('verify').map(({ key, label, caption }) => ({ key, label, caption })),
    )
  })

  it('scan isolation completed+error is degraded not success', () => {
    expect(dagVisualStatus({ status: 'completed', output: { status: 'failed' } })).toBe(
      'degraded',
    )
    expect(dagVisualStatus({ status: 'completed', error_message: 'stderr' })).toBe('degraded')
    expect(dagVisualStatus({ status: 'skipped' })).toBe('skipped')
    expect(dagVisualStatus({ status: 'failed' })).toBe('failed')
    expect(dagVisualStatus({ status: 'blocked' })).toBe('blocked')
  })

  it('fitGraphToView scales the graph up to fill the canvas', () => {
    const fit = fitGraphToView({ width: 400, height: 120 }, { width: 1000, height: 360 }, 20)
    expect(fit.scale).toBeCloseTo(Math.min(960 / 400, 344 / 120, 1.25), 5)
    expect(fit.overflowY).toBe(false)
    expect(fit.tx).toBeGreaterThan(0)
    expect(fit.ty).toBeGreaterThan(0)
  })

  it('does not shrink below 1 so labels stay readable', () => {
    const fit = fitGraphToView({ width: 2000, height: 400 }, { width: 800, height: 300 }, 20)
    expect(fit.scale).toBe(1)
    expect(fit.overflowX).toBe(true)
  })

  it('does not create a vertical scrollbar when the graph is shorter than the canvas', () => {
    const fit = fitGraphToView({ width: 1600, height: 220 }, { width: 900, height: 360 }, 24)
    expect(fit.overflowY).toBe(false)
    expect(fit.slotH).toBe(360)
    expect(fit.ty + 220).toBeLessThanOrEqual(360)
  })

  it('forks 1:n in the column gap, not on the target edge', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const osv = layout.nodes.find((n) => n.key === 'scan_osv')!
    const edge = layout.edges.find((e) => e.from === 'source' && e.to === 'scan_osv')!
    expect(edge.d).toContain(' H ')
    expect(edge.d).not.toMatch(new RegExp(`H ${osv.x} V`))
    const cluster = layout.nodes.find((n) => n.key === 'cluster')!
    const gitleaksMerge = layout.edges.find((e) => e.from === 'scan_gitleaks' && e.to === 'cluster')!
    expect((gitleaksMerge.d.match(/ V /g) ?? []).length).toBeGreaterThanOrEqual(1)
    const merge = layout.edges.find((e) => e.from === 'scan_osv' && e.to === 'cluster')!
    expect(merge.d).not.toMatch(new RegExp(`H ${cluster.x} V`))
    const support = layout.edges.find((e) => e.from === 'env_ready' && e.to === 'lead_verify')!
    expect(support.d).toContain(' V ')
    expect(support.labelX).toBeGreaterThan(nodeCenterX(osv))
  })

  it('contain mode shrinks into the strip so the page tabs keep height', () => {
    const fit = fitGraphToView(
      { width: 1600, height: 260 },
      { width: 900, height: 120 },
      16,
      { contain: true },
    )
    expect(fit.overflowY).toBe(false)
    expect(fit.overflowX).toBe(false)
    expect(fit.slotH).toBe(120)
    expect(fit.scale).toBeLessThan(1)
    expect(fit.ty + 260 * fit.scale).toBeLessThanOrEqual(120 + 0.5)
  })
})

function nodeCenterX(node: { x: number; width: number }): number {
  return node.x + node.width / 2
}
