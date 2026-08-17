import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, RefObject, WheelEvent as ReactWheelEvent } from 'react'

/**
 * 流式内容的「贴底跟随」：贴在底部时随新内容滚动，用户上翻后停住不打扰。
 *
 * 只看滚动位置是不够的 —— 内容在同一帧里变长会把位置重新拽回底部，
 * 用户的上翻意图就丢了。所以向上的手势（滚轮 / 触摸 / 按键）直接解除跟随，
 * 位置判定只负责「用户自己滚回底部时重新吸附」。
 */

export interface ScrollMetrics {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}

/** 距底部小于该像素数即视为贴底（留出一行的容差）。 */
export const STICK_TO_BOTTOM_THRESHOLD = 48

/** 会把视口往上带的按键，按下即解除跟随。 */
export const SCROLL_UP_KEYS = new Set(['ArrowUp', 'PageUp', 'Home'])

export function distanceFromBottom(m: ScrollMetrics): number {
  return Math.max(0, m.scrollHeight - m.scrollTop - m.clientHeight)
}

export function isNearBottom(m: ScrollMetrics, threshold = STICK_TO_BOTTOM_THRESHOLD): boolean {
  return distanceFromBottom(m) <= threshold
}

interface UseStickToBottomOptions {
  enabled?: boolean
  threshold?: number
}

interface StickToBottomHandlers {
  onScroll: () => void
  onWheel: (event: ReactWheelEvent) => void
  onTouchMove: () => void
  onKeyDown: (event: ReactKeyboardEvent) => void
}

interface UseStickToBottomResult {
  /** 挂在滚动容器上 */
  scrollRef: RefObject<HTMLDivElement | null>
  /** 挂在内容包裹层上，用于感知流式增长（不只是条数变化） */
  contentRef: RefObject<HTMLDivElement | null>
  handlers: StickToBottomHandlers
  pinned: boolean
  scrollToBottom: () => void
}

export function useStickToBottom(
  contentKey: unknown,
  { enabled = true, threshold = STICK_TO_BOTTOM_THRESHOLD }: UseStickToBottomOptions = {},
): UseStickToBottomResult {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const [pinned, setPinned] = useState(true)
  // 手势要立刻生效，等不到 React 提交，所以状态另存一份 ref
  const pinnedRef = useRef(true)
  const selfScrollRef = useRef(false)

  const applyPinned = useCallback((next: boolean) => {
    pinnedRef.current = next
    setPinned(next)
  }, [])

  const jumpToBottom = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    selfScrollRef.current = true
    el.scrollTop = el.scrollHeight
  }, [])

  const scrollToBottom = useCallback(() => {
    applyPinned(true)
    jumpToBottom()
  }, [applyPinned, jumpToBottom])

  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = isNearBottom(el, threshold)
    // 自己滚到底触发的事件不能反过来推翻用户刚做出的上翻决定
    if (selfScrollRef.current) {
      selfScrollRef.current = false
      if (atBottom) return
    }
    applyPinned(atBottom)
  }, [applyPinned, threshold])

  const onWheel = useCallback(
    (event: ReactWheelEvent) => {
      if (event.deltaY < 0) applyPinned(false)
    },
    [applyPinned],
  )

  const onTouchMove = useCallback(() => {
    applyPinned(false)
  }, [applyPinned])

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent) => {
      if (SCROLL_UP_KEYS.has(event.key)) applyPinned(false)
    },
    [applyPinned],
  )

  useEffect(() => {
    if (enabled && pinned) jumpToBottom()
  }, [contentKey, enabled, pinned, jumpToBottom])

  // 同一条消息持续变长、折叠展开都不改条数，靠尺寸变化补齐
  useEffect(() => {
    const content = contentRef.current
    if (!content || !enabled || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (pinnedRef.current) jumpToBottom()
    })
    observer.observe(content)
    return () => observer.disconnect()
  }, [enabled, jumpToBottom])

  return {
    scrollRef,
    contentRef,
    handlers: { onScroll, onWheel, onTouchMove, onKeyDown },
    pinned,
    scrollToBottom,
  }
}
