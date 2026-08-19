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

  it('highlights fenced python and shows a copyable code slab', () => {
    const html = renderToStaticMarkup(
      <MarkdownBody source={'```python\ndef ping():\n    return 1\n```'} />,
    )
    expect(html).toMatch(/hljs-keyword/)
    expect(html).toContain('def')
    expect(html).toMatch(/crucible-codeblock/)
    expect(html).toMatch(/python/i)
    expect(html).toMatch(/复制/)
  })

  it('keeps inline code as a pill without codeblock chrome', () => {
    const html = renderToStaticMarkup(<MarkdownBody source={'用 `os.system` 调用'} />)
    expect(html).toMatch(/<code[^>]*>os\.system<\/code>/)
    expect(html).not.toMatch(/crucible-codeblock/)
    expect(html).not.toMatch(/hljs-keyword/)
  })
})
