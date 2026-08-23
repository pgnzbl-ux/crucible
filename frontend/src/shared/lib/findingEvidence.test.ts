import { describe, expect, it } from 'vitest'

import { formatSourceToSink } from './findingEvidence'

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
