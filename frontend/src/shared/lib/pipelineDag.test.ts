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

  it('keeps the backend audit dependency even when verify hides skipped discovery nodes', () => {
    expect(PIPELINE_REQUIRES.audit).toEqual(['source', 'profile', 'dispatch'])
  })
})

describe('pipelineDag 运行流程', () => {
  it('uses the backend ready waves in the shared discovery stage model', () => {
    const stages = pipelineOverviewStages('discovery')
    expect(stages[1].nodeKeys).toEqual(['profile', 'scan_gitleaks', 'scan_osv'])
    expect(stages[1].parallel).toBe(true)
    expect(stages[2].nodeKeys).toEqual(['scan_semgrep', 'env_ready'])
    expect(stages[2].parallel).toBe(true)
    expect(stages.find((stage) => stage.key === 'review')?.parallel).toBeUndefined()
    expect(stages.at(-2)?.nodeKeys).toEqual(['lead_verify'])
  })

  it('discovery: keeps dependency lanes readable before lead verification', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const x = (k: string) => layout.nodes.find((n) => n.key === k)!.x
    const y = (k: string) => layout.nodes.find((n) => n.key === k)!.y
    const node = (k: string) => layout.nodes.find((n) => n.key === k)!

    expect(x('source')).toBeLessThan(x('profile'))
    expect(x('profile')).toBeLessThan(x('scan_semgrep'))
    expect(x('scan_semgrep')).toBeLessThan(x('cluster'))
    expect(x('cluster')).toBeCloseTo(x('triage'), 0)
    expect(x('dispatch')).toBeLessThan(x('lead_verify'))
    expect(x('lead_verify')).toBeLessThan(x('report'))

    expect(node('scan_osv').x).toBeCloseTo(node('scan_gitleaks').x, 0)
    expect(node('profile').x).toBeCloseTo(node('scan_osv').x, 0)
    expect(node('profile').x).toBeLessThan(node('scan_semgrep').x)
    expect(node('scan_semgrep').x).toBeCloseTo(node('env_ready').x, 0)
    expect(y('scan_gitleaks')).toBeLessThan(y('profile'))
    expect(y('profile')).toBeLessThan(y('scan_osv'))
    expect(y('profile')).toBeCloseTo(y('scan_semgrep'), 0)
    expect(y('scan_semgrep')).toBeLessThan(y('env_ready'))

    expect(layout.nodes.some((n) => n.key === 'is_web')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'audit')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'reproduce')).toBe(false)
    expect(node('over').shape).toBe('terminal')
    expect(Math.min(...layout.nodes.map((n) => n.y))).toBeGreaterThanOrEqual(0)
    expect(flowColumn('profile', 'discovery')).toBe(flowColumn('scan_gitleaks', 'discovery'))
    expect(flowColumn('scan_gitleaks', 'discovery')).toBeLessThan(flowColumn('scan_semgrep', 'discovery'))
    expect(flowColumn('scan_semgrep', 'discovery')).toBe(flowColumn('env_ready', 'discovery'))
    expect(layout.groups.map((group) => group.key)).toEqual([
      'source',
      'initial',
      'deep',
      'review',
      'dispatch',
      'verify',
      'report',
    ])
    expect(layout.groups.map(({ key, label, caption }) => ({ key, label, caption }))).toEqual(
      pipelineOverviewStages('discovery').map(({ key, label, caption }) => ({ key, label, caption })),
    )
  })

  it('stacks 发现复核 nodes in one column with vertical arrows', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const node = (k: string) => layout.nodes.find((n) => n.key === k)!

    expect(node('cluster').x).toBeCloseTo(node('screen').x, 0)
    expect(node('screen').x).toBeCloseTo(node('triage').x, 0)
    expect(node('cluster').y).toBeLessThan(node('screen').y)
    expect(node('screen').y).toBeLessThan(node('triage').y)
    expect(flowColumn('cluster', 'discovery')).toBe(flowColumn('screen', 'discovery'))
    expect(flowColumn('screen', 'discovery')).toBe(flowColumn('triage', 'discovery'))
    expect(flowColumn('triage', 'discovery')).toBeLessThan(flowColumn('dispatch', 'discovery'))

    const screenToTriage = layout.edges.find((e) => e.from === 'screen' && e.to === 'triage')!
    expect(screenToTriage.kind).toBe('flow')
    expect(screenToTriage.d).toMatch(/^M [\d.]+ [\d.]+ V [\d.]+$/)

    const stepped = layout.edges.find((e) => e.from === 'cluster' && e.to === 'screen')!
    expect(stepped.kind).toBe('flow')
    expect(stepped.d).toMatch(/^M [\d.]+ [\d.]+ V [\d.]+$/)

    const review = layout.groups.find((g) => g.key === 'review')!
    const source = layout.groups.find((g) => g.key === 'source')!
    expect(review.width).toBeCloseTo(source.width, 0)
  })

  it('merges three scan edges into cluster at a shared junction', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const cluster = layout.nodes.find((n) => n.key === 'cluster')!
    const cy = cluster.y + cluster.height / 2
    const intoCluster = (from: string) =>
      layout.edges.find((e) => e.from === from && e.to === 'cluster')!

    const gitleaks = intoCluster('scan_gitleaks')
    const osv = intoCluster('scan_osv')
    const semgrep = intoCluster('scan_semgrep')
    const finalSeg = new RegExp(`V ${cy} H ${cluster.x}$`)
    expect(gitleaks.d).toMatch(finalSeg)
    expect(osv.d).toMatch(finalSeg)
    expect(semgrep.d).toMatch(finalSeg)

    const mergeXs = [gitleaks, osv, semgrep].map((edge) => {
      const m = edge.d.match(new RegExp(`H ([\\d.]+) V ${cy} H ${cluster.x}$`))
      expect(m).toBeTruthy()
      return Number(m![1])
    })
    expect(mergeXs[0]).toBeCloseTo(mergeXs[1], 5)
    expect(mergeXs[1]).toBeCloseTo(mergeXs[2], 5)
    expect(mergeXs[0]).toBeLessThan(cluster.x)
  })

  it('discovery edges match runtime waves and LeadWorker handoff', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'discovery' })
    const edges = flowEdges(layout.nodes.map((n) => n.key), 'discovery')
    expect(edges).toContainEqual(expect.objectContaining({ from: 'source', to: 'scan_osv' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'source', to: 'scan_gitleaks' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'profile', to: 'scan_semgrep' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'scan_gitleaks', to: 'cluster' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'env_ready', to: 'lead_verify' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'dispatch', to: 'lead_verify' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'lead_verify', to: 'report' }))
    expect(edges.some((e) => e.from === 'is_web' || e.to === 'is_web')).toBe(false)
    expect(edges.some((e) => e.from === 'audit' || e.to === 'audit')).toBe(false)
    expect(edges).toContainEqual(expect.objectContaining({
      from: 'profile',
      to: 'env_ready',
      kind: 'conditional',
      label: '仅 Web',
    }))
    expect(edges).toContainEqual(expect.objectContaining({
      from: 'env_ready',
      to: 'lead_verify',
      kind: 'support',
    }))
  })

  it('keeps the backbone connected when dispatch is not on the canvas yet', () => {
    const keys = PIPELINE_NODE_ORDER.filter((k) => k !== 'dispatch')
    const layout = layoutPipelineDag(keys, { mode: 'discovery' })
    const edges = flowEdges(layout.nodes.map((n) => n.key), 'discovery')
    expect(edges).toContainEqual(expect.objectContaining({ from: 'triage', to: 'report' }))
    expect(edges.some((e) => e.from === 'dispatch' || e.to === 'dispatch')).toBe(false)
  })

  it('verify: hidden discovery nodes follow the current sequential wave order', () => {
    const visible = PIPELINE_NODE_ORDER.filter((k) => !VERIFY_MODE_SKIPPED_KEYS.has(k))
    const layout = layoutPipelineDag(visible, { mode: 'verify' })
    const node = (k: string) => layout.nodes.find((n) => n.key === k)!

    expect(layout.nodes.some((n) => n.key === 'cluster')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'dispatch')).toBe(false)
    expect(node('env_ready').x).toBeLessThan(node('audit').x)
    expect(node('audit').x).toBeLessThan(node('reproduce').x)
    expect(layout.nodes.some((n) => n.key === 'is_web')).toBe(false)
    expect(layout.nodes.some((n) => n.key === 'lead_verify')).toBe(false)

    const edges = flowEdges(
      layout.nodes.map((n) => n.key),
      'verify',
    )
    expect(edges).toContainEqual(expect.objectContaining({ from: 'profile', to: 'env_ready' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'env_ready', to: 'audit' }))
    expect(edges).toContainEqual(expect.objectContaining({ from: 'audit', to: 'reproduce' }))
    expect(edges.some((e) => e.from === 'dispatch')).toBe(false)
  })

  it('verify expanded graph keeps skipped discovery chain and does not switch to LeadWorker', () => {
    const layout = layoutPipelineDag(PIPELINE_NODE_ORDER, { mode: 'verify' })
    expect(layout.nodes.some((n) => n.key === 'dispatch')).toBe(true)
    expect(layout.nodes.some((n) => n.key === 'lead_verify')).toBe(false)
    expect(layout.edges).toContainEqual(expect.objectContaining({ from: 'dispatch', to: 'audit' }))
    expect(layout.edges).toContainEqual(expect.objectContaining({
      from: 'env_ready',
      to: 'audit',
      kind: 'support',
    }))
    expect(layout.groups.map((group) => group.key)).toEqual([
      'source',
      'initial',
      'deep',
      'review',
      'dispatch',
      'audit',
      'reproduce',
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
    expect(gitleaksMerge.d.match(/ V /g) ?? []).toHaveLength(1)
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
