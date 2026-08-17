import { App } from 'antd'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ReportContent } from './ReportContent'
import type { ReportDetail } from '../lib/api'

const base = {
  id: 'r1',
  task_id: 't1',
  run_id: 'u1',
  owner_id: 'o1',
  status: 'generated',
  conclusion: 'unconfirmed',
  title: '报告',
  summary: null,
  reasoning: null,
  evidence_summary: null,
  artifact_key: null,
  md_artifact_key: null,
  docx_artifact_key: null,
  published_at: null,
  created_at: '',
  updated_at: '',
  evidence: [],
  verdict: null,
  cvss_score: null,
  severity: null,
  vulnerable_file: null,
  report_data: null,
} as ReportDetail

describe('ReportContent', () => {
  it('renders markdown sections and index columns', () => {
    const html = renderToStaticMarkup(
      <App>
        <ReportContent
          report={{
            ...base,
            verdict: 'confirmed',
            cvss_score: 9.8,
            severity: 'Critical',
            vulnerable_file: 'app/login.py',
            report_data: {
              product_intro: '这是 **产品**',
              vulnerability: 'CWE-89',
              impact: 'all',
              details: '`file.py`',
              reproduction: '步骤 1',
              poc_commands: '```bash\ncurl x\n```',
              fix_suggestions: '参数化',
              reporting_decision: '报送',
            },
          }}
        />
      </App>,
    )
    expect(html).toMatch(/<strong>产品<\/strong>/)
    expect(html).toContain('app/login.py')
    expect(html).toContain('9.8')
  })

  it('shows upgrade hint for nested leftover JSON', () => {
    const html = renderToStaticMarkup(
      <App>
        <ReportContent
          report={{
            ...base,
            verdict: null,
            cvss_score: null,
            severity: null,
            vulnerable_file: null,
            report_data: { product_intro: { nested: true } },
          }}
        />
      </App>,
    )
    expect(html).toMatch(/报告格式已升级/)
  })
})
