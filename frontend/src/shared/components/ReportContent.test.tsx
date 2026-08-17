import type { ReactNode } from 'react'
import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ReportContent } from './ReportContent'
import type { ReportDetail } from '../lib/api'

/** AuditPanel 走 useQuery 拉 NodeRun，静态渲染下拿不到数据，只需提供 provider。 */
function render(node: ReactNode): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return renderToStaticMarkup(
    <QueryClientProvider client={qc}>
      <App>{node}</App>
    </QueryClientProvider>,
  )
}

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
  poc_language: null,
  poc_filename: null,
  poc_code: null,
  poc_usage: null,
  report_data: null,
} as ReportDetail

describe('ReportContent', () => {
  it('renders markdown sections and index columns', () => {
    const html = render(
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
      />,
    )
    expect(html).toMatch(/<strong>产品<\/strong>/)
    expect(html).toContain('app/login.py')
    expect(html).toContain('9.8')
  })

  it('shows verification record notice without poc or cvss', () => {
    const html = render(
      <ReportContent
        report={{
          ...base,
          title: '漏洞验证记录',
          verdict: 'not_reproduced',
          cvss_score: null,
          severity: null,
          vulnerable_file: 'server/agent.routes.ts',
          report_data: {
            document_kind: 'verification_record',
            product_intro: '产品Y',
            claimed_issue: '声称命令注入',
            whitebox_analysis: '入口可达',
            test_record: 'curl 返回 CLI missing',
            blocker: '没有 Claude Code 二进制',
            observed_facts: '未观察到危害',
            remaining_conditions: '需要可用 CLI',
            reporting_decision: '不报送',
          },
        }}
      />,
    )
    expect(html).toContain('未形成漏洞 PoC/CVSS')
    expect(html).toContain('未评定')
    expect(html).toContain('声称问题')
    expect(html).toContain('测试记录')
    expect(html).not.toMatch(/§6 POC/)
    expect(html).toContain('导出验证记录')
  })

  it('shows upgrade hint for nested leftover JSON', () => {
    const html = render(
      <ReportContent
        report={{
          ...base,
          verdict: null,
          cvss_score: null,
          severity: null,
          vulnerable_file: null,
          report_data: { product_intro: { nested: true } },
        }}
      />,
    )
    expect(html).toMatch(/报告格式已升级/)
  })

  it('renders poc_code fence instead of leftover curl markdown', () => {
    const html = render(
      <ReportContent
        report={{
          ...base,
          verdict: 'confirmed',
          poc_language: 'python',
          poc_filename: 'poc.py',
          poc_code: "print('FROM_COLUMN')\n",
          poc_usage: 'python poc.py --url http://x',
          report_data: {
            product_intro: 'p',
            vulnerability: 'v',
            impact: 'i',
            details: 'd',
            reproduction: 'r',
            poc_commands: '```bash\ncurl leftover\n```',
            fix_suggestions: 'f',
            reporting_decision: 's',
          },
        }}
      />,
    )
    expect(html).toContain('FROM_COLUMN')
    expect(html).toContain('poc.py')
    expect(html).not.toContain('curl leftover')
  })
})
