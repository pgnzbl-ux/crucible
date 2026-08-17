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
})
