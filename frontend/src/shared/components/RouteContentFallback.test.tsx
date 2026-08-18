import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { RouteContentFallback } from './RouteContentFallback'

describe('RouteContentFallback', () => {
  it('只在内容区提示加载，不铺满视口', () => {
    const html = renderToStaticMarkup(<RouteContentFallback />)
    expect(html).toContain('aria-label="页面加载中"')
    expect(html).not.toContain('100vh')
    expect(html).not.toContain('ant-spin')
    expect(html).toContain('crucible-route-progress')
  })
})
