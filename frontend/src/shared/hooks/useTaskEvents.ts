/**
 * useTaskEvents — 订阅任务实时事件流（SSE）
 *
 * 设计要点：
 * - 浏览器原生 EventSource，无需额外依赖
 * - 自动断线重连：onerror 后 delay 重连（指数退避 1s→2s→4s 上限 10s）
 * - 客户端组件卸载自动关闭
 * - token 通过 query 注入（EventSource 不支持自定义 header）
 * - 自管重连带 last_event_id，服务端跳过已回放 sequence
 *
 * 返回：
 * - events: 累积的全部事件数组（包含历史回放 + 实时推送）
 * - status: idle | connecting | open | reconnecting | closed
 * - error: 最近一次错误
 * - reset: 清空事件列表（用于任务切换时）
 */

import { useEffect, useRef, useState, useCallback } from 'react'

import { api, isUnauthorizedError } from '../lib/api'
import { buildTaskEventStreamUrl } from '../lib/taskEventStream'

export interface SSEEvent<T = unknown> {
  type: string // event_type
  sequence?: number
  run_id?: string
  event: T
  replayed?: boolean
  correlation_id?: string
}

export type SSEStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

interface UseTaskEventsOptions {
  /** 是否启用（默认 true） */
  enabled?: boolean
  /** 重连最大延迟（毫秒），默认 10000 */
  maxReconnectDelay?: number
  /** 客户端最多保留的事件数，默认 1000 */
  maxEvents?: number
  /** 攒批落状态的间隔（毫秒），默认 200 */
  flushIntervalMs?: number
}

export function useTaskEvents<T = unknown>(
  taskId: string | null,
  options: UseTaskEventsOptions = {},
) {
  const {
    enabled = true,
    maxReconnectDelay = 10_000,
    maxEvents = 1_000,
    flushIntervalMs = 200,
  } = options
  const [events, setEvents] = useState<SSEEvent<T>[]>([])
  const [status, setStatus] = useState<SSEStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  const eventSourceRef = useRef<EventSource | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const reconnectTimerRef = useRef<number | null>(null)
  const closedByUnmountRef = useRef(false)
  const seenEventKeysRef = useRef(new Set<string>())
  // Agent 输出是突发的，逐帧 setState 会把整条渲染链按帧数放大
  const pendingRef = useRef<SSEEvent<T>[]>([])
  const flushTimerRef = useRef<number | null>(null)
  const errorRef = useRef<string | null>(null)

  const reset = useCallback(() => {
    pendingRef.current = []
    setEvents([])
    errorRef.current = null
    setError(null)
    seenEventKeysRef.current.clear()
  }, [])

  useEffect(() => {
    if (!enabled || !taskId) {
      setStatus('idle')
      return
    }

    closedByUnmountRef.current = false
    seenEventKeysRef.current.clear()
    pendingRef.current = []
    setEvents([])
    setStatus('connecting')

    const token = localStorage.getItem('crucible_token')
    const lastEventIdRef = { current: 0 as number }

    let es: EventSource | null = null

    const cleanup = () => {
      if (es) {
        es.close()
        es = null
      }
      if (reconnectTimerRef.current != null) {
        window.clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      if (flushTimerRef.current != null) {
        window.clearTimeout(flushTimerRef.current)
        flushTimerRef.current = null
      }
    }

    const flush = () => {
      flushTimerRef.current = null
      const batch = pendingRef.current
      if (batch.length === 0) return
      pendingRef.current = []
      setEvents((prev) => {
        const next = prev.concat(batch)
        if (next.length <= maxEvents) return next
        const removed = next.slice(0, next.length - maxEvents)
        for (const event of removed) {
          if (event.sequence != null) {
            seenEventKeysRef.current.delete(`${event.run_id ?? ''}:${event.sequence}`)
          }
        }
        return next.slice(-maxEvents)
      })
    }

    const scheduleFlush = () => {
      if (flushTimerRef.current != null) return
      flushTimerRef.current = window.setTimeout(flush, flushIntervalMs)
    }

    const connect = () => {
      if (closedByUnmountRef.current) return
      const urlString = buildTaskEventStreamUrl({
        origin: window.location.origin,
        taskId,
        token,
        lastEventId: lastEventIdRef.current,
      })
      const newEs = new EventSource(urlString)
      es = newEs
      eventSourceRef.current = newEs

      newEs.onmessage = (e: MessageEvent) => {
        try {
          const parsed = JSON.parse(e.data) as SSEEvent<T>
          // ready 帧也走 onmessage(后端不发 event: 具名行,统一 type 字段)
          if (parsed.type === 'ready') {
            setStatus('open')
            reconnectAttemptsRef.current = 0
            return
          }
          if (typeof parsed.sequence === 'number' && parsed.sequence > lastEventIdRef.current) {
            lastEventIdRef.current = parsed.sequence
          }
          // 去重：同一 run 的 sequence 不重复（历史回放 + 实时推送短暂重叠）
          const eventKey = parsed.sequence == null
            ? null
            : `${parsed.run_id ?? ''}:${parsed.sequence}`
          if (eventKey && seenEventKeysRef.current.has(eventKey)) return
          if (eventKey) seenEventKeysRef.current.add(eventKey)
          pendingRef.current.push(parsed)
          scheduleFlush()
          if (errorRef.current !== null) {
            errorRef.current = null
            setError(null)
          }
        } catch (err) {
          console.warn('[useTaskEvents] 解析 SSE 帧失败:', err, e.data)
        }
      }

      newEs.onerror = () => {
        if (closedByUnmountRef.current) return
        if (newEs.readyState === EventSource.CLOSED) {
          void (async () => {
            try {
              await api.me()
            } catch (error) {
              if (isUnauthorizedError(error) || !localStorage.getItem('crucible_token')) {
                setStatus('closed')
                errorRef.current = '登录已过期，请重新登录'
                setError(errorRef.current)
                return
              }
            }
            if (closedByUnmountRef.current) return
            const attempt = reconnectAttemptsRef.current + 1
            reconnectAttemptsRef.current = attempt
            const delay = Math.min(1000 * 2 ** (attempt - 1), maxReconnectDelay)
            setStatus('reconnecting')
            errorRef.current = `连接中断，${Math.round(delay / 1000)}s 后重连...`
            setError(errorRef.current)
            reconnectTimerRef.current = window.setTimeout(() => {
              cleanup()
              connect()
            }, delay)
          })()
        }
      }
    }

    connect()

    return () => {
      closedByUnmountRef.current = true
      cleanup()
      eventSourceRef.current = null
      setStatus('closed')
    }
  }, [taskId, enabled, maxEvents, maxReconnectDelay, flushIntervalMs])

  return { events, status, error, reset }
}