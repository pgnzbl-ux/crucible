import { describe, expect, it } from 'vitest'

import {
  distanceFromBottom,
  isNearBottom,
  SCROLL_UP_KEYS,
  STICK_TO_BOTTOM_THRESHOLD,
} from './useStickToBottom'

const viewport = (scrollTop: number) => ({ scrollTop, scrollHeight: 1000, clientHeight: 400 })

describe('stick-to-bottom 判定', () => {
  it('贴在底部时继续跟随', () => {
    expect(isNearBottom(viewport(600))).toBe(true)
  })

  it('容差内的轻微上移仍算贴底', () => {
    expect(isNearBottom(viewport(600 - STICK_TO_BOTTOM_THRESHOLD))).toBe(true)
  })

  it('用户明显上翻后不再跟随', () => {
    expect(isNearBottom(viewport(600 - STICK_TO_BOTTOM_THRESHOLD - 1))).toBe(false)
    expect(isNearBottom(viewport(0))).toBe(false)
  })

  it('内容不足一屏时视为贴底', () => {
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 200, clientHeight: 400 })).toBe(true)
  })

  it('距底距离不为负', () => {
    expect(distanceFromBottom(viewport(600))).toBe(0)
    expect(distanceFromBottom(viewport(900))).toBe(0)
    expect(distanceFromBottom(viewport(100))).toBe(500)
  })

  it('自定义容差生效', () => {
    expect(isNearBottom(viewport(400), 200)).toBe(true)
    expect(isNearBottom(viewport(400), 100)).toBe(false)
  })

  it('只有往上带视口的按键解除跟随', () => {
    expect([...SCROLL_UP_KEYS]).toEqual(['ArrowUp', 'PageUp', 'Home'])
    expect(SCROLL_UP_KEYS.has('ArrowDown')).toBe(false)
    expect(SCROLL_UP_KEYS.has('End')).toBe(false)
  })
})
