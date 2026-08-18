import { afterEach, describe, expect, it, vi } from 'vitest'

import { downloadAuthenticated, fetchAuthenticatedText } from './download'

describe('downloadAuthenticated', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('sends bearer token and triggers a file download', async () => {
    localStorage.setItem('crucible_token', 'tok-1')
    const blob = new Blob(['# report'], { type: 'text/markdown' })
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => blob,
    })
    vi.stubGlobal('fetch', fetchMock)
    const createObjectURL = vi.fn(() => 'blob:report')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })

    const click = vi.fn()
    const originalCreate = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'a') {
        return { click, set href(_v: string) {}, set download(_v: string) {}, remove() {} } as unknown as HTMLAnchorElement
      }
      return originalCreate(tag)
    })

    await downloadAuthenticated('/api/v1/reports/r1/export?format=md', 'report.md')

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/reports/r1/export?format=md', {
      headers: { Authorization: 'Bearer tok-1' },
    })
    expect(click).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:report')
  })

  it('throws on 401', async () => {
    localStorage.setItem('crucible_token', 'expired')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
      }),
    )
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { pathname: '/login', href: '/login' },
    })

    await expect(downloadAuthenticated('/api/v1/reports/r1/export?format=json', 'r.json')).rejects.toThrow(
      '登录已过期',
    )
    expect(localStorage.getItem('crucible_token')).toBeNull()
  })

  it('fetchAuthenticatedText returns markdown body', async () => {
    localStorage.setItem('crucible_token', 'tok-1')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        text: async () => '## 1. 产品介绍\n\nhello',
      }),
    )
    await expect(fetchAuthenticatedText('/api/v1/reports/r1/export?format=md')).resolves.toBe(
      '## 1. 产品介绍\n\nhello',
    )
  })

  it('parses error envelope instead of stringifying detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ error: { code: 'BUSY', message: '报告正在生成' } }),
      }),
    )
    await expect(downloadAuthenticated('/api/v1/reports/r1/export?format=md', 'r.md')).rejects.toMatchObject({
      message: '报告正在生成',
      status: 409,
    })
  })
})
