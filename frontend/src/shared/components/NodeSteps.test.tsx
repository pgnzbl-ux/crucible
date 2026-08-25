import type { ReactNode } from 'react'
import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { NodeRun } from '../lib/api'
import { NodeSteps } from './NodeSteps'

const LONG_GATE_REASON =
  'Q1 核心主张：受保护资产为宿主文件系统。Q2 链路连通：projectPath 直达 cwd。Q3 结构性阻断：无结构性阻断。'

function renderWithNodes(nodes: NodeRun[], extra?: ReactNode, taskType: 'verify' | 'discovery' = 'verify'): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  qc.setQueryData(['run-nodes', 't1', 'r1'], nodes)
  return renderToStaticMarkup(
    <QueryClientProvider client={qc}>
      <App>
        {extra}
        <NodeSteps taskId="t1" runId="r1" compact={false} taskType={taskType} />
      </App>
    </QueryClientProvider>,
  )
}

describe('NodeSteps audit detail wiring', () => {
  it('完成态 audit 走 AuditDetail 分区，不把整段 gate_reason 当 pre-wrap 摘要', () => {
    const html = renderWithNodes([
      {
        id: 'n-audit',
        node_index: 3,
        node_key: 'audit',
        status: 'completed',
        attempt: 1,
        error_message: null,
        started_at: null,
        finished_at: null,
        output: {
          gate_verdict: 'pass',
          gate_reason: LONG_GATE_REASON,
          runtime_dependent: true,
          kill_chain: 'POST /api/agent -> validateApiKey -> Bash 执行',
          defense_layers: [{ layer: 'validateExternalApiKey', bypassed: '平台模式放行' }],
          payloads: [{ request: 'POST /api/agent', expectation: '回显 /etc/shadow' }],
        },
      },
    ])

    expect(html).toContain('Q1 · 核心主张')
    expect(html).toContain('运行时依赖')
    expect(html).toContain('利用链 · 3 步')
    expect(html).not.toContain('Gate 失败 ·')
    expect(html).not.toContain(`Gate 通过 · ${LONG_GATE_REASON}`)
  })

  it('失败的 reproduce 节点在可重试任务上显示「从本节点重试」', () => {
    const html = renderWithNodes(
      [
        {
          id: 'n-repro',
          node_index: 4,
          node_key: 'reproduce',
          status: 'failed',
          attempt: 1,
          error_message: '靶场无响应',
          started_at: null,
          finished_at: null,
          output: {},
        },
      ],
      undefined,
    )
    // 默认不传 onRetryFromNode → 不显示按钮
    expect(html).not.toContain('从本节点重试')
  })

  it('传入 onRetryFromNode 且 task 失败时，失败节点渲染重试按钮', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], [
      {
        id: 'n-repro',
        node_index: 4,
        node_key: 'reproduce',
        status: 'failed',
        attempt: 1,
        error_message: '靶场无响应',
        started_at: null,
        finished_at: null,
        output: {},
      },
    ])
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps
            taskId="t1"
            runId="r1"
            taskStatus="failed"
            compact={false}
            onRetryFromNode={() => undefined}
          />
        </App>
      </QueryClientProvider>,
    )
    expect(html).toContain('从本节点重试')
  })

  it('keeps the full node error log for test triage', () => {
    const html = renderWithNodes(
      [
        {
          id: 'n-scan',
          node_index: 2,
          node_key: 'scan_gitleaks',
          status: 'completed',
          attempt: 1,
          error_message: 'gitleaks 退出码 2\nsecret scanner crashed: db locked',
          started_at: null,
          finished_at: null,
          output: { status: 'failed', engine: 'gitleaks' },
        },
      ],
      undefined,
      'discovery',
    )
    expect(html).toContain('crucible-node-error-log')
    expect(html).toContain('db locked')
    expect(html).toContain('is-degraded')
  })

  it('progress tab is a node list, not a second flowchart', () => {
    const html = renderWithNodes(stubNodes())
    expect(html).toContain('crucible-node-list')
    expect(html).not.toContain('crucible-dag-node')
  })

  it('renders the progress tab as a staged vertical timeline with an overall status', () => {
    const html = renderWithNodes([
      {
        id: 'n-source',
        node_index: 0,
        node_key: 'source',
        status: 'completed',
        attempt: 1,
        error_message: null,
        started_at: null,
        finished_at: null,
        output: {},
      },
      {
        id: 'n-profile',
        node_index: 1,
        node_key: 'profile',
        status: 'running',
        attempt: 1,
        error_message: null,
        started_at: null,
        finished_at: null,
        output: { progress: '正在识别框架' },
      },
    ])

    expect(html).toContain('crucible-node-progress__summary')
    expect(html).toContain('role="progressbar"')
    expect(html).toContain('aria-valuenow="17"')
    expect(html).toContain('显示跳过的节点(9)')
    expect(html).not.toContain('data-node-key="api_hunt"')
    expect(html).not.toContain('data-node-key="api_inventory"')
    expect(html).toContain('正在执行：项目画像')
    expect(html).toContain('阶段 01 · 准备源码')
    expect(html).toContain('crucible-node-list__connector')
    expect(html).toContain('crucible-node-list__status is-running')
  })

  it('clicking an executed node in the vertical progress selects its runtime log', async () => {
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    const { act } = await import('react')
    const { createRoot } = await import('react-dom/client')
    const clicks: string[] = []
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], [{
      id: 'n-source', node_index: 0, node_key: 'source', status: 'completed', attempt: 1,
      error_message: null, started_at: null, finished_at: null, output: {},
    }] satisfies NodeRun[])
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <App>
            <NodeSteps
              taskId="t1"
              runId="r1"
              compact={false}
              onSelectNode={(key) => clicks.push(key)}
            />
          </App>
        </QueryClientProvider>,
      )
    })

    const source = container.querySelector('[data-node-key="source"]')
    const pending = container.querySelector('[data-node-key="audit"]')
    expect(source?.getAttribute('role')).toBe('button')
    expect(pending?.getAttribute('role')).toBeNull()
    await act(async () => {
      source?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      pending?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(clicks).toEqual(['source'])
    await act(async () => root.unmount())
    container.remove()
  })

  it('shows only the actual failed node in red and keeps downstream nodes unexecuted', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], [
      {
        id: 'n-source', node_index: 0, node_key: 'source', status: 'completed', attempt: 1,
        error_message: null, started_at: null, finished_at: null, output: {},
      },
      {
        id: 'n-profile', node_index: 1, node_key: 'profile', status: 'completed', attempt: 1,
        error_message: null, started_at: null, finished_at: null, output: {},
      },
      {
        id: 'n-env', node_index: 5, node_key: 'env_ready', status: 'failed', attempt: 3,
        error_message: '靶场启动失败', started_at: null, finished_at: null, output: {},
      },
    ] satisfies NodeRun[])
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps taskId="t1" runId="r1" taskStatus="failed" compact={false} />
        </App>
      </QueryClientProvider>,
    )

    expect((html.match(/crucible-node-list__item is-failed/g) ?? [])).toHaveLength(1)
    expect(html).toContain('crucible-node-list__item is-failed" data-node-key="env_ready"')
    expect(html).toContain('crucible-node-list__item is-blocked" data-node-key="audit"')
    expect(html).toContain('crucible-node-list__item is-blocked" data-node-key="reproduce"')
    expect(html).toContain('crucible-node-list__item is-blocked" data-node-key="report"')
    expect(html).toContain('上游必要节点失败，本节点未执行')
    expect(html).toContain('crucible-node-list__status is-blocked">未执行')
  })

  it('discovery vertical progress replaces placeholder audit nodes with LeadWorker status', () => {
    const html = renderWithNodes([
      {
        id: 'n-dispatch',
        node_index: 8,
        node_key: 'dispatch',
        status: 'completed',
        attempt: 1,
        error_message: null,
        started_at: null,
        finished_at: null,
        output: { has_lead: true, queued_count: 2 },
      },
      {
        id: 'n-audit',
        node_index: 9,
        node_key: 'audit',
        status: 'skipped',
        attempt: 1,
        error_message: null,
        started_at: null,
        finished_at: null,
        output: {},
      },
      {
        id: 'n-reproduce',
        node_index: 10,
        node_key: 'reproduce',
        status: 'skipped',
        attempt: 1,
        error_message: null,
        started_at: null,
        finished_at: null,
        output: {},
      },
    ], undefined, 'discovery')

    expect(html).toContain('data-node-key="lead_verify"')
    expect(html).toContain('正在执行：多线索终认')
    expect(html).toContain('正在终认 2 条线索')
    expect(html).not.toContain('data-node-key="audit"')
    expect(html).not.toContain('data-node-key="reproduce"')
  })
})

function stubNodes(): NodeRun[] {
  return [
    {
      id: 'n-audit',
      node_index: 3,
      node_key: 'audit',
      status: 'completed',
      attempt: 1,
      error_message: null,
      started_at: null,
      finished_at: null,
      output: { gate_verdict: 'pass' },
    },
  ]
}

function runningPipeline(): NodeRun[] {
  const base = {
    attempt: 1,
    error_message: null,
    started_at: null,
    finished_at: null,
    output: {},
  }
  return [
    { ...base, id: 'n-source', node_index: 0, node_key: 'source', status: 'completed' },
    { ...base, id: 'n-profile', node_index: 1, node_key: 'profile', status: 'completed' },
    { ...base, id: 'n-env', node_index: 2, node_key: 'env_ready', status: 'running' },
  ]
}

describe('NodeSteps compact topology', () => {
  it('uses the same blocked status in collapsed stages and expanded nodes', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], [
      {
        id: 'n-source', node_index: 0, node_key: 'source', status: 'completed', attempt: 1,
        error_message: null, started_at: null, finished_at: null, output: {},
      },
      {
        id: 'n-profile', node_index: 1, node_key: 'profile', status: 'completed', attempt: 1,
        error_message: null, started_at: null, finished_at: null, output: {},
      },
      {
        id: 'n-env', node_index: 5, node_key: 'env_ready', status: 'failed', attempt: 1,
        error_message: '靶场启动失败', started_at: null, finished_at: null, output: {},
      },
    ] satisfies NodeRun[])
    const render = (expanded: boolean) => renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps taskId="t1" runId="r1" taskStatus="failed" compact expanded={expanded} />
        </App>
      </QueryClientProvider>,
    )

    const collapsed = render(false)
    const expanded = render(true)
    expect((collapsed.match(/crucible-dag-stage-card is-failed/g) ?? [])).toHaveLength(1)
    expect(collapsed).toContain('crucible-dag-stage-card is-blocked')
    expect(expanded).toContain('crucible-dag-node crucible-dag-node--env_ready is-failed')
    expect(expanded).toContain('crucible-dag-node crucible-dag-node--audit is-blocked')
    expect(expanded).toContain('crucible-dag-node crucible-dag-node--reproduce is-blocked')
    expect(expanded).toContain('crucible-dag-node crucible-dag-node--report is-blocked')
  })

  it('clicking the active business stage selects its running node for the event stream', async () => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }),
    })
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

    const { act } = await import('react')
    const { createRoot } = await import('react-dom/client')
    const clicks: string[] = []
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], runningPipeline())
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <App>
            <NodeSteps
              taskId="t1"
              runId="r1"
              compact
              onSelectNode={(key) => clicks.push(key)}
            />
          </App>
        </QueryClientProvider>,
      )
    })
    const running = container.querySelector('[data-stage-key="env"]')
    expect(running).toBeTruthy()
    await act(async () => {
      running?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(clicks).toEqual(['env_ready'])
    await act(async () => {
      root.unmount()
    })
    container.remove()
  })

  it('clicking a DAG edge does not jump to the event stream', async () => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }),
    })
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

    const { act } = await import('react')
    const { createRoot } = await import('react-dom/client')
    const clicks: string[] = []
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], runningPipeline())
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <App>
            <NodeSteps
              taskId="t1"
              runId="r1"
              compact
              expanded
              onSelectNode={(key) => clicks.push(key)}
            />
          </App>
        </QueryClientProvider>,
      )
    })
    const edge = container.querySelector('[data-edge-from]')
    expect(edge).toBeTruthy()
    await act(async () => {
      edge?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(clicks).toEqual([])
    await act(async () => {
      root.unmount()
    })
    container.remove()
  })

  it('does not select a pending business stage', async () => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }),
    })
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

    const { act } = await import('react')
    const { createRoot } = await import('react-dom/client')
    const clicks: string[] = []
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], runningPipeline())
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <App>
            <NodeSteps
              taskId="t1"
              runId="r1"
              compact
              onSelectNode={(key) => clicks.push(key)}
            />
          </App>
        </QueryClientProvider>,
      )
    })
    const pending = container.querySelector('[data-stage-key="audit"]')
    expect(pending).toBeTruthy()
    await act(async () => {
      pending?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(clicks).toEqual([])
    await act(async () => {
      root.unmount()
    })
    container.remove()
  })

  it('marks the selected node so the flowchart can drive the event stream', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], stubNodes())
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps
            taskId="t1"
            runId="r1"
            compact
            selectedNode="audit"
            onSelectNode={() => undefined}
          />
        </App>
      </QueryClientProvider>,
    )
    expect(html).toContain('data-stage-key="audit"')
    expect(html).toContain('is-selected')
  })

  it('default overview uses mode-specific stages; expanded discovery shows detailed nodes', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const nodes = [
      ...runningPipeline(),
      {
        id: 'n-gitleaks',
        node_index: 2,
        node_key: 'scan_gitleaks',
        status: 'completed',
        attempt: 1,
        error_message: null,
        started_at: null,
        finished_at: null,
        output: {},
      },
    ]
    qc.setQueryData(['run-nodes', 't1', 'r1'], nodes)
    const verify = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps taskId="t1" runId="r1" compact taskType="verify" />
        </App>
      </QueryClientProvider>,
    )
    const discovery = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps taskId="t1" runId="r1" compact taskType="discovery" />
        </App>
      </QueryClientProvider>,
    )
    const discoveryDetailed = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps taskId="t1" runId="r1" compact expanded taskType="discovery" />
        </App>
      </QueryClientProvider>,
    )
    expect(verify).toContain('crucible-dag-overview')
    expect(verify).toContain('定向验证')
    // 收起态阶段卡：含 AI 节点的阶段带角标（verify: profile/env/audit/reproduce/report）
    expect((verify.match(/data-ai="true"/g) ?? []).length).toBeGreaterThanOrEqual(4)
    expect(discovery).toContain('crucible-dag-overview')
    // discovery 收起：并行初筛/深度/复核/终认/报告 等含 AI
    expect((discovery.match(/data-ai="true"/g) ?? []).length).toBeGreaterThanOrEqual(4)
    expect(discovery).toContain('crucible-dag-stage-card__ai')
    expect(verify).toContain('data-stage-key="audit"')
    expect(verify).not.toContain('data-stage-key="initial"')
    expect(discovery).toContain('crucible-dag-overview')
    expect(discovery).toContain('仓库审计')
    expect(discovery).toContain('data-stage-key="initial"')
    expect(discovery).toContain('data-stage-key="deep"')
    expect(discovery).toContain('画像 + Gitleaks + OSV')
    expect(discovery).toContain('Semgrep · API 清单 · Web 靶场')
    expect(discovery).toContain('data-stage-key="verify"')
    expect(discoveryDetailed).toContain('crucible-dag-node--scan_gitleaks')
    expect(discoveryDetailed).toContain('crucible-dag-node--dispatch')
    expect(discoveryDetailed).toContain('crucible-dag-node--lead_verify')
    expect(discoveryDetailed).not.toContain('crucible-dag-node--audit')
    expect(discoveryDetailed).toContain('data-group-key="initial"')
    expect(discoveryDetailed).toContain('data-group-key="deep"')
    for (const label of ['准备源码', '并行初筛', '深度分析', '线索归并', '扫描复核', '线索调度', '多线索终认', '审计报告']) {
      expect(discovery).toContain(label)
      expect(discoveryDetailed).toContain(label)
    }
    expect(discoveryDetailed).toContain('data-edge-from="triage"')
  })

  it('discovery overview totals include hunt plus merged lead_verify usage', () => {
    const usage = (
      prompt: number,
      completion: number,
      cacheRead = 0,
    ) => ({
      prompt_tokens: prompt,
      completion_tokens: completion,
      cache_read_input_tokens: cacheRead,
      cache_creation_input_tokens: 0,
      total_tokens: prompt + completion + cacheRead,
    })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], [
      {
        id: 'n-profile', node_index: 1, node_key: 'profile', status: 'completed',
        attempt: 1, error_message: null, started_at: null, finished_at: null,
        output: {}, usage: usage(10, 2),
      },
      {
        id: 'n-hunt', node_index: 8, node_key: 'api_hunt', status: 'completed',
        attempt: 1, error_message: null, started_at: null, finished_at: null,
        output: { qualified_count: 1 }, usage: usage(40, 8),
      },
      {
        id: 'n-dispatch', node_index: 11, node_key: 'dispatch', status: 'completed',
        attempt: 1, error_message: null, started_at: null, finished_at: null,
        output: { has_lead: true, queued_count: 1 },
      },
      {
        id: 'n-audit', node_index: 12, node_key: 'audit', status: 'skipped',
        attempt: 1, error_message: null, started_at: null, finished_at: null,
        output: {}, usage: usage(100, 20, 50),
      },
      {
        id: 'n-repro', node_index: 13, node_key: 'reproduce', status: 'skipped',
        attempt: 1, error_message: null, started_at: null, finished_at: null,
        output: {}, usage: usage(30, 5),
      },
      {
        id: 'n-report', node_index: 14, node_key: 'report', status: 'completed',
        attempt: 1, error_message: null, started_at: null, finished_at: null,
        output: {},
      },
    ] satisfies NodeRun[])
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps taskId="t1" runId="r1" compact taskType="discovery" />
        </App>
      </QueryClientProvider>,
    )
    expect(html).toContain('data-stage-key="clues"')
    expect(html).toContain('data-ai="true"')
    // 10+2 + 40+8 + 100+20+50 + 30+5 = 265
    expect(html).toContain('crucible-dag-overview__total')
    expect(html).toContain('265 tok')
  })

  it('discovery compact shows LeadWorker running after dispatch queues leads', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], [
      ...runningPipeline(),
      {
        id: 'n-dispatch',
        node_index: 8,
        node_key: 'dispatch',
        status: 'completed',
        attempt: 1,
        error_message: null,
        started_at: null,
        finished_at: null,
        output: { has_lead: true, queued_count: 2 },
      },
    ])
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps taskId="t1" runId="r1" compact taskType="discovery" taskStatus="running" />
        </App>
      </QueryClientProvider>,
    )
    expect(html).toContain('data-stage-key="verify"')
    expect(html).toContain('crucible-dag-stage-card is-running')
    expect(html).toContain('多线索终认')
  })

  it('discovery compact skips LeadWorker when dispatch has no eligible lead', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], [
      ...runningPipeline(),
      {
        id: 'n-dispatch',
        node_index: 8,
        node_key: 'dispatch',
        status: 'completed',
        attempt: 1,
        error_message: null,
        started_at: null,
        finished_at: null,
        output: { has_lead: false, queued_count: 0 },
      },
    ])
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps taskId="t1" runId="r1" compact taskType="discovery" />
        </App>
      </QueryClientProvider>,
    )
    expect(html).toContain('data-stage-key="verify"')
    expect(html).toContain('crucible-dag-stage-card is-skipped')
  })

  it('compact pin offers expand so the tabs keep the page', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['run-nodes', 't1', 'r1'], runningPipeline())
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <NodeSteps taskId="t1" runId="r1" compact onToggleExpand={() => undefined} />
        </App>
      </QueryClientProvider>,
    )
    expect(html).toContain('展开流程图')
  })
})
