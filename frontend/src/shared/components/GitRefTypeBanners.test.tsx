import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { GitRefTypeBanners } from './GitRefTypeBanners'

describe('GitRefTypeBanners', () => {
  it('渲染三个引用类型横幅', () => {
    const html = renderToStaticMarkup(<GitRefTypeBanners value="branch" />)
    expect(html).toContain('分支')
    expect(html).toContain('标签')
    expect(html).toContain('提交')
    expect(html).toContain('branch')
    expect(html).toContain('tag')
    expect(html).toContain('commit')
  })

  it('选中态带 selected class', () => {
    const html = renderToStaticMarkup(<GitRefTypeBanners value="tag" />)
    expect(html).toContain('crucible-ref-banner--selected')
    expect(html).toContain('aria-checked="true"')
  })

  it('点击横幅触发 onChange', () => {
    const onChange = vi.fn()
    // SSR 无交互；校验 props 可挂载即可（交互由 Form 集成）
    const html = renderToStaticMarkup(<GitRefTypeBanners value="commit" onChange={onChange} />)
    expect(html).toContain('提交')
    expect(onChange).not.toHaveBeenCalled()
  })
})
