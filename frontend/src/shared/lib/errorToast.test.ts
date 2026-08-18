import { describe, expect, it } from 'vitest'

import { errorToastText, nextErrorToast } from './errorToast'

describe('errorToastText', () => {
  it('prefers Error.message then string then fallback', () => {
    expect(errorToastText(new Error('邮箱或密码错误'), '登录失败')).toBe('邮箱或密码错误')
    expect(errorToastText('无法连接认证服务', '加载失败')).toBe('无法连接认证服务')
    expect(errorToastText(null, '加载失败')).toBe('加载失败')
    expect(errorToastText(new Error('  '), '加载失败')).toBe('加载失败')
  })
})

describe('nextErrorToast', () => {
  it('emits once per distinct error until recovered', () => {
    const first = nextErrorToast(null, true, new Error('超时'), '加载失败')
    expect(first).toEqual({ lastText: '超时', toast: '超时' })
    const same = nextErrorToast(first.lastText, true, new Error('超时'), '加载失败')
    expect(same.toast).toBeNull()
    const recovered = nextErrorToast(same.lastText, false, null, '加载失败')
    expect(recovered).toEqual({ lastText: null, toast: null })
    const again = nextErrorToast(recovered.lastText, true, new Error('超时'), '加载失败')
    expect(again.toast).toBe('超时')
  })
})
