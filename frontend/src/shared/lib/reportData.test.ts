import { describe, expect, it } from 'vitest'

import { asMarkdownSection, asRecord, asRecordArray, asStringArray, REPORT_SECTIONS } from './reportData'

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
  })

  it('only accepts non-empty markdown strings', () => {
    expect(asMarkdownSection('一段 **md**')).toBe('一段 **md**')
    expect(asMarkdownSection('  ')).toBeNull()
    expect(asMarkdownSection({ type: 'CWE-89' })).toBeNull()
    expect(asMarkdownSection(['curl'])).toBeNull()
  })
})
