import { describe, expect, it } from 'vitest'

import {
  evidenceMetaFromFinding,
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
