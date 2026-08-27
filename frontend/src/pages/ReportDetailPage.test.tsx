import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Router } from 'wouter'

import type { ReportDetail } from '../shared/lib/api'
import { ReportDetailPage } from './ReportDetailPage'

const report: ReportDetail = {
  id: 'r1',
  task_id: 't1',
  run_id: 'u1',
  owner_id: 'o1',
  status: 'generated',
  product_name: 'DemoApp',
  affected_version: 'main @ abc1234',
  project_address: 'https://github.com/demo/app',
  conclusion: 'unconfirmed',
  title: '验证报告',
  summary: null,
  reasoning: '很长的报告正文需要滚动阅读。',
  evidence_summary: null,
  artifact_key: null,
  verdict: 'confirmed',
  cvss_score: null,
  severity: null,
  vulnerable_file: null,
  poc_language: null,
  poc_filename: null,
  poc_code: null,
  poc_usage: null,
  report_data: null,
  md_artifact_key: null,
  docx_artifact_key: null,
  published_at: null,
  created_at: '',
  updated_at: '',
  evidence: [],
}

describe('ReportDetailPage fill layout', () => {
  it('uses fill container and fill tabs so long reports can scroll', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['report', 'r1'], report)
    const html = renderToStaticMarkup(
      <QueryClientProvider client={qc}>
        <App>
          <Router ssrPath="/reports/r1">
            <ReportDetailPage />
          </Router>
        </App>
      </QueryClientProvider>,
    )
    expect(html).toContain('crucible-page-container--fill')
    expect(html).toContain('crucible-fill-tabs')
  })
})
