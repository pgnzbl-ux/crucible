import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Router } from 'wouter'

import type { Project } from '../shared/lib/api'
import { ProjectDetailPage } from './ProjectDetailPage'

const gitProject: Project = {
  id: 'p1',
  name: 'claudecodeui',
  git_url: 'https://github.com/siteboon/claudecodeui',
  source_type: 'git',
  default_ref: 'main',
  default_ref_type: 'branch',
  description: '桌面客户端',
  owner_id: 'u1',
  detected_language: 'TypeScript',
  detected_framework: 'React',
  is_web: true,
  last_cloned_at: null,
  created_at: '',
  updated_at: '',
}

describe('ProjectDetailPage 编辑入口', () => {
  it('详情页提供编辑按钮并展示备注', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['project', 'p1'], gitProject)
    qc.setQueryData(['project-artifacts', 'p1'], { items: [], total: 0 })
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <Router ssrPath="/projects/p1">
            <ProjectDetailPage />
          </Router>
        </App>
      </QueryClientProvider>,
    )
    expect(html).toContain('编辑项目')
    expect(html).toContain('桌面客户端')
    expect(html).toContain('备注')
  })
})
