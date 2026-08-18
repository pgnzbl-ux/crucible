import { describe, expect, it } from 'vitest'

import { markdownUrlTransform, safeHttpUrl } from './safeUrl'

describe('safeHttpUrl', () => {
  it.each([
    ['http://198.18.0.1:3002', 'http://198.18.0.1:3002/'],
    ['https://example.com/a', 'https://example.com/a'],
  ])('放行 %s', (raw, href) => {
    expect(safeHttpUrl(raw)).toBe(href)
  })

  it.each([
    'javascript:alert(1)',
    'data:text/html,hi',
    'vbscript:x',
    '//evil.example/phish',
    '/relative',
    '',
    '  ',
    null,
    1,
  ])('拒绝 %j', (raw) => {
    expect(safeHttpUrl(raw)).toBeNull()
  })
})

describe('markdownUrlTransform', () => {
  it('keeps relative and fragment links', () => {
    expect(markdownUrlTransform('#sec')).toBe('#sec')
    expect(markdownUrlTransform('/reports/1')).toBe('/reports/1')
    expect(markdownUrlTransform('./shot.png')).toBe('./shot.png')
  })

  it('strips javascript and protocol-relative', () => {
    expect(markdownUrlTransform('javascript:alert(1)')).toBe('')
    expect(markdownUrlTransform('data:text/html,x')).toBe('')
    expect(markdownUrlTransform('//evil.example')).toBe('')
  })

  it('allows https', () => {
    expect(markdownUrlTransform('https://example.com/a')).toBe('https://example.com/a')
  })
})
