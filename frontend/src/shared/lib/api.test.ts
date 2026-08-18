import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('api error envelope', () => {
  it('prefers error.message over the legacy detail field', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        status: 404,
        ok: false,
        json: async () => ({
          error: { code: 'TASK_NOT_FOUND', message: '任务不存在', details: {} },
          detail: '被兼容字段盖住也不该用这个',
        }),
      }),
    )

    await expect(api.retryTask('missing')).rejects.toThrow('任务不存在')
    await expect(api.retryTask('missing')).rejects.toMatchObject({ status: 404, code: 'TASK_NOT_FOUND' })
    await expect(api.retryTask('missing')).rejects.toBeInstanceOf(ApiError)
  })

  it('falls back to detail.message for LAB_IN_USE-style objects', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        status: 409,
        ok: false,
        json: async () => ({
          detail: { code: 'LAB_IN_USE', message: '靶场正被任务使用', task_ids: ['t1'] },
        }),
      }),
    )

    await expect(api.labAction('lab-1', 'stop')).rejects.toThrow('靶场正被任务使用')
    await expect(api.labAction('lab-1', 'stop')).rejects.toMatchObject({ status: 409, code: 'LAB_IN_USE' })
  })

  it('login 401 展示服务端原因，不当成会话过期', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        status: 401,
        ok: false,
        json: async () => ({
          error: { code: 'UNAUTHENTICATED', message: '邮箱或密码错误' },
          detail: '邮箱或密码错误',
        }),
      }),
    )

    await expect(api.login({ email: 'a@b.c', password: 'wrong' })).rejects.toMatchObject({
      message: '邮箱或密码错误',
      status: 401,
      code: 'UNAUTHENTICATED',
    })
    expect(localStorage.getItem('crucible_token')).toBeNull()
  })

  it('带 token 的 401 才清会话', async () => {
    localStorage.setItem('crucible_token', 'expired')
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { pathname: '/tasks', href: '/tasks' },
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        status: 401,
        ok: false,
        json: async () => ({ error: { message: '凭据无效或已过期' } }),
      }),
    )

    await expect(api.getTask('t1')).rejects.toThrow('登录已过期，请重新登录')
    expect(localStorage.getItem('crucible_token')).toBeNull()
  })

  it('uploadEvidence reads the same error envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        status: 400,
        ok: false,
        json: async () => ({
          error: { code: 'EVIDENCE_REJECTED', message: '文件类型不允许' },
        }),
      }),
    )
    const file = new File(['x'], 'a.exe', { type: 'application/octet-stream' })
    await expect(api.uploadEvidence('r1', file)).rejects.toMatchObject({
      message: '文件类型不允许',
      status: 400,
      code: 'EVIDENCE_REJECTED',
    })
  })
})
