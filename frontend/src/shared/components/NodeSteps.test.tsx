import type { ReactNode } from 'react'
import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { NodeRun } from '../lib/api'
import { NodeSteps } from './NodeSteps'

const LONG_GATE_REASON =
  'Q1 核心主张：受保护资产为宿主文件系统。Q2 链路连通：projectPath 直达 cwd。Q3 结构性阻断：无结构性阻断。'

function renderWithNodes(nodes: NodeRun[], extra?: ReactNode): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  qc.setQueryData(['run-nodes', 't1', 'r1'], nodes)
  return renderToStaticMarkup(
    <QueryClientProvider client={qc}>
      <App>
        {extra}
        <NodeSteps taskId="t1" runId="r1" compact={false} />
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

  it('non-compact rows without onSelectNode are not clickable locators', () => {
    const html = renderWithNodes(stubNodes())
    expect(html).not.toContain('is-selectable')
    expect(html).toContain('crucible-node-row')
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

describe('NodeSteps compact selection', () => {
  it('clicking the currently running node still selects it for the event stream', async () => {
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
    const running = container.querySelector('[data-node-key="env_ready"]')
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

  it('clicking compact step content does not jump to the event stream', async () => {
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
    const caption = container.querySelector('[data-node-caption="env_ready"]')
    expect(caption).toBeTruthy()
    await act(async () => {
      caption?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(clicks).toEqual([])
    await act(async () => {
      root.unmount()
    })
    container.remove()
  })

  it('does not select a pending node from the compact flowchart', async () => {
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
    const pending = container.querySelector('[data-node-key="audit"]')
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
    expect(html).toContain('crucible-node-step--audit')
    expect(html).toContain('is-selected')
    expect(html).toContain('aria-pressed')
  })
})
