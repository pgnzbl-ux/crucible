import { describe, expect, it } from 'vitest'

import { asMarkdownSection, asRecord, asRecordArray, asStringArray, documentKindOf, formatDenoiseFunnel, pocToMarkdown, RECORD_SECTIONS, REPORT_SECTIONS, sectionsFor } from './reportData'

describe('reportData guards', () => {
  it('accepts plain records and rejects arrays and primitives', () => {
    expect(asRecord({ title: 'demo' })).toEqual({ title: 'demo' })
    expect(asRecord(['bad'])).toEqual({})
    expect(asRecord('bad')).toEqual({})
  })

  it('keeps only plain records in record arrays', () => {
    expect(asRecordArray([{ step: 1 }, null, 'bad', ['bad']])).toEqual([{ step: 1 }])
    expect(asRecordArray({ step: 1 })).toEqual([])
  })

  it('keeps only strings in string arrays', () => {
    expect(asStringArray(['one', 2, null, 'two'])).toEqual(['one', 'two'])
    expect(asStringArray('one')).toEqual([])
  })

  it('has 8 report sections', () => {
    expect(REPORT_SECTIONS).toHaveLength(8)
    expect(REPORT_SECTIONS.map((s) => s.key)).toContain('product_intro')
    expect(REPORT_SECTIONS.map((s) => s.key)).toContain('poc_commands')
  })

  it('has 8 verification record sections without poc', () => {
    expect(RECORD_SECTIONS).toHaveLength(8)
    expect(RECORD_SECTIONS.map((s) => s.key)).toContain('test_record')
    expect(RECORD_SECTIONS.map((s) => s.key)).not.toContain('poc_commands')
  })

  it('selects sections by document_kind and defaults old reports to vulnerability report', () => {
    expect(documentKindOf({ document_kind: 'verification_record' })).toBe('verification_record')
    expect(documentKindOf({})).toBe('vulnerability_report')
    expect(sectionsFor({ document_kind: 'verification_record' })).toBe(RECORD_SECTIONS)
    expect(sectionsFor({ product_intro: 'old' })).toBe(REPORT_SECTIONS)
  })

  it('only accepts non-empty markdown strings', () => {
    expect(asMarkdownSection('一段 **md**')).toBe('一段 **md**')
    expect(asMarkdownSection('  ')).toBeNull()
    expect(asMarkdownSection({ type: 'CWE-89' })).toBeNull()
    expect(asMarkdownSection(['curl'])).toBeNull()
  })

  it('builds python fence from poc columns', () => {
    const md = pocToMarkdown('python', "print('x')\n", 'python poc.py --url http://x')
    expect(md).toContain('```python')
    expect(md).toContain("print('x')")
    expect(md).toContain('用法：`python poc.py --url http://x`')
  })

  it('returns null when poc code is empty', () => {
    expect(pocToMarkdown('python', '  ', 'x')).toBeNull()
  })

  it('formats denoise funnel from audit_summary', () => {
    expect(formatDenoiseFunnel({
      audit_summary: {
        denoise_funnel: {
          finding_count: 40,
          dropped_c_count: 12,
          group_count: 8,
          bypass_count: 3,
        },
      },
    })).toBe('原始 40 → C档 12 → 复核组 8 → 依赖情报 3')
    expect(formatDenoiseFunnel({})).toBeNull()
  })
})
