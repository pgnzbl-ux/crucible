import { describe, expect, it } from 'vitest'

import {
  displaySourcePath,
  evidenceMetaFromFinding,
  findingEvidenceView,
  formatSourceToSink,
  ruleClassLabel,
} from './findingEvidence'

describe('formatSourceToSink', () => {
  it('joins normalized string steps', () => {
    expect(formatSourceToSink(['app.py:10 (request.args)', 'db.py:42 (execute)']))
      .toBe('app.py:10 (request.args) → db.py:42 (execute)')
  })

  it('renders structured flow steps without object coercion', () => {
    expect(formatSourceToSink([
      { file: 'app.py', line: 10, expression: 'request.args' },
      { path: 'db.py', line_number: 42, label: 'execute' },
    ])).toBe('app.py:10 (request.args) → db.py:42 (execute)')
  })

  it('keeps unknown JSON evidence readable and omits empty steps', () => {
    expect(formatSourceToSink([null, { kind: 'sink' }, '']))
      .toBe('{"kind":"sink"}')
  })
})

describe('evidenceMetaFromFinding', () => {
  it('prefers raw.has_dataflow and rule_class', () => {
    expect(evidenceMetaFromFinding({
      source_to_sink: null,
      raw: { has_dataflow: true, rule_class: 'known', confidence: 'high' },
    })).toEqual({
      hasDataflow: true,
      ruleClass: 'known',
      confidence: 'HIGH',
    })
  })

  it('infers dataflow from source_to_sink when raw missing', () => {
    expect(evidenceMetaFromFinding({
      source_to_sink: ['a.py:1'],
      raw: {},
    }).hasDataflow).toBe(true)
  })
})

describe('ruleClassLabel', () => {
  it('maps known and generic', () => {
    expect(ruleClassLabel('known')).toBe('已知厂商规则')
    expect(ruleClassLabel('generic')).toBe('泛匹配/熵规则')
    expect(ruleClassLabel(null)).toBeNull()
  })
})

describe('displaySourcePath', () => {
  it('keeps short relative paths', () => {
    expect(displaySourcePath('requirements.txt')).toBe('requirements.txt')
  })

  it('shortens absolute lockfile paths', () => {
    expect(displaySourcePath('/abs/workspace/repo/frontend/package-lock.json'))
      .toBe('repo/frontend/package-lock.json')
  })
})

describe('findingEvidenceView', () => {
  it('renders gitleaks secret instead of a REDACTED placeholder', () => {
    const view = findingEvidenceView({
      engine: 'gitleaks',
      rule_id: 'aws-access-token-id',
      message: 'AWS Access Key ID 命中 config/prod.env:8',
      file_path: 'config/prod.env',
      line_start: 8,
      code_snippet: 'AKIAIOSFODNN7EXAMPLE',
      raw: { rule_class: 'known', description: 'AWS Access Key ID', commit: 'abc123' },
    })
    expect(view.cardTitle).toBe('泄露详情')
    expect(view.body).toContain('AKIAIOSFODNN7EXAMPLE')
    expect(view.redacted).toBe(false)
    expect(view.fields.some((field) => field.value.includes('已知厂商规则'))).toBe(true)
  })

  it('flags historical REDACTED gitleaks snippets', () => {
    const view = findingEvidenceView({
      engine: 'gitleaks',
      rule_id: 'generic-api-key',
      message: 'generic-api-key has detected secret for file a.env.',
      file_path: 'a.env',
      line_start: 1,
      code_snippet: 'REDACTED',
      raw: { rule_class: 'generic' },
    })
    expect(view.redacted).toBe(true)
  })

  it('renders osv packages as an advisory instead of a CVSS vector dump', () => {
    const view = findingEvidenceView({
      engine: 'osv',
      rule_id: 'GHSA-7ww5-4wqc-8m2g',
      message: 'jinja2 2.11.3 存在依赖漏洞：JinjaXSS（CVE-2024-22195，中危）',
      file_path: '/abs/workspace/repo/requirements.txt',
      severity: 'warning',
      raw: {
        dependency_name: 'jinja2',
        version: '2.11.3',
        ecosystem: 'PyPI',
        cve: 'CVE-2024-22195',
        aliases: ['CVE-2024-22195'],
        cvss: 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H',
        cvss_score: 5.4,
        summary: 'JinjaXSS',
        called: false,
        fixed_versions: ['3.1.3'],
      },
    })
    expect(view.cardTitle).toBe('依赖漏洞详情')
    expect(view.bodyKind).toBe('advisory')
    expect(view.fields.find((field) => field.label === '依赖')?.value).toContain('jinja2 2.11.3')
    expect(view.fields.find((field) => field.label === '严重度')?.value).toContain('中危')
    expect(view.fields.find((field) => field.label === '严重度')?.value).not.toContain('CVSS:3.1')
    expect(view.fields.find((field) => field.label === '可达性')?.value).toContain('未被调用')
    expect(view.links[0]?.href).toContain('GHSA-7ww5-4wqc-8m2g')
  })
})
