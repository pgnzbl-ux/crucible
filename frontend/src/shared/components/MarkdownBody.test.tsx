import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { MarkdownBody } from './MarkdownBody'

describe('MarkdownBody', () => {
  it('renders report headings as h2 instead of preformatted text', () => {
    const html = renderToStaticMarkup(<MarkdownBody source={'## 1. 产品介绍\n\n一段介绍'} />)
    expect(html).toMatch(/<h2[^>]*>[\s\S]*产品介绍/)
    expect(html).toContain('一段介绍')
    expect(html).not.toMatch(/<pre[^>]*>[\s\S]*产品介绍/)
  })

  it('renders GFM tables used by report export', () => {
    const md = '| 项 | 内容 |\n|---|---|\n| 漏洞类型 | XSS |'
    const html = renderToStaticMarkup(<MarkdownBody source={md} />)
    expect(html).toMatch(/<table/)
    expect(html).toContain('漏洞类型')
    expect(html).toContain('XSS')
  })

  it('does not emit executable HTML from untrusted agent output', () => {
    const html = renderToStaticMarkup(
      <MarkdownBody source={'<script>alert(1)</script>\n\n**确认**'} />,
    )
    expect(html).not.toMatch(/<script[\s>]/i)
    expect(html).toMatch(/<strong>确认<\/strong>/)
  })

  it('does not turn javascript: links into clickable hrefs', () => {
    const html = renderToStaticMarkup(<MarkdownBody source={'[x](javascript:alert(1))'} />)
    expect(html).not.toContain('javascript:')
  })
})
