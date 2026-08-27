import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Router } from 'wouter'

import type { AgentEvent, NodeRun, TaskDetail } from '../../../shared/lib/api'
import { TaskDetailTabs } from './TaskDetailTabs'

const task: TaskDetail = {
  id: 't1',
  project_address: 'https://example.com/demo.git',
  project_id: null,
  project_ref: 'main',
  project_ref_type: 'branch',
  clone_depth: 1,
  status: 'running',
  verdict: null,
  priority: 'normal',
  source_type: 'git',
  task_type: 'verify',
  finding_count: 0,
  pending_review_count: 0,
  confirmed_count: 0,
  report_status: null,
  owner_id: 'u1',
  created_at: '',
  updated_at: '',
  vulnerability_description: 'test',
  vulnerability_reasoning: null,
  credential_refs: [],
  runs: [{
    id: 'r1', task_id: 't1', status: 'running', started_at: null, finished_at: null,
    error_message: null, created_at: '',
  }],
}

const nodes: NodeRun[] = [{
  id: 'n1', node_index: 0, node_key: 'source', status: 'completed', attempt: 1,
  error_message: null, started_at: null, finished_at: null, output: {},
}]

const events: AgentEvent[] = [{
  id: 'e1', run_id: 'r1', sequence: 1, event_type: 'node.updated',
  payload: { node_key: 'source', status: 'completed' }, source: 'rest', created_at: '',
}]

describe('TaskDetailTabs merged audit process', () => {
  it('renders progress and runtime events side by side without a separate events tab', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['task', 't1'], task)
    qc.setQueryData(['task-events', 't1'], events)
    qc.setQueryData(['run-nodes', 't1', 'r1'], nodes)
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <Router ssrPath="/tasks/t1?tab=progress">
            <TaskDetailTabs taskId="t1" activeTab="progress" onTabChange={() => undefined} />
          </Router>
        </App>
      </QueryClientProvider>,
    )

    expect(html).toContain('crucible-audit-workbench')
    expect(html).toContain('crucible-audit-workbench__pane is-progress')
    expect(html).toContain('crucible-audit-workbench__pane is-events')
    expect(html).toContain('选择节点，在右侧查看对应运行日志')
    expect(html).toContain('Agent 过程流')
    expect(html).toContain('审计过程')
    expect(html).not.toContain('>运行日志</div>')
  })
})
