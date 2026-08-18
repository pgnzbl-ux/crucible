import { App } from 'antd'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { EnvReadyDetail } from './EnvReadyDetail'

function render(output: Record<string, unknown>): string {
  return renderToStaticMarkup(
    <App>
      <EnvReadyDetail output={output} />
    </App>,
  )
}

describe('EnvReadyDetail', () => {
  it('把预设账密摆出来给研究员直接用', () => {
    const html = render({
      target_url: 'http://198.18.0.1:3002',
      initial_creds: { username: 'admin', password: 'admin123', login_url: '/login' },
    })
    expect(html).toContain('http://198.18.0.1:3002')
    expect(html).toContain('admin123')
    expect(html).toContain('/login')
  })

  it('Agent 确认免登录时说免登录', () => {
    const html = render({
      target_url: 'http://198.18.0.1:3002',
      initial_creds: { auth_required: false, note: '平台模式跳过鉴权' },
    })
    expect(html).toContain('免登录')
    expect(html).toContain('平台模式跳过鉴权')
  })

  it('凭据缺失时点明是没挖到，不冒充免登录', () => {
    const html = render({ target_url: 'http://198.18.0.1:3002', initial_creds: {} })
    expect(html).toContain('无预设凭据')
    expect(html).not.toContain('免登录')
  })

  it('javascript: 靶场地址不当成可点击链接', () => {
    const html = render({ target_url: 'javascript:alert(1)' })
    expect(html).not.toMatch(/href=["']javascript:/i)
    expect(html).toContain('javascript:alert(1)')
  })
})
