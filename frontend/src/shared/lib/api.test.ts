import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'

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
  })
})
