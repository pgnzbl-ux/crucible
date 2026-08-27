/**
 * useTaskEvents — 订阅任务实时事件流（SSE）
 *
 * 设计要点：
 * - 采用标准 fetch + ReadableStream，通过 Authorization: Bearer <token> 请求头鉴权，
 *   彻底避免在 URL Query 中携带凭据，杜绝 Nginx access.log / 浏览器历史记录泄露
 * - 自动断线重连：遇到网络中断后指数退避重连（1s→2s→4s 上限 10s）
 * - 客户端组件卸载通过 AbortController 自动中止连接
 * - 自管重连通过 Last-Event-ID 请求头传递 sequence，服务端跳过已回放事件
 * - 事件批量落库调度（flushIntervalMs 缓冲），防止高频 Agent 输出导致频繁渲染
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

  const abortControllerRef = useRef<AbortController | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const reconnectTimerRef = useRef<number | null>(null)
  const closedByUnmountRef = useRef(false)
  const seenEventKeysRef = useRef(new Set<string>())
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

    const lastEventIdRef = { current: 0 as number }

    const cleanup = () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
        abortControllerRef.current = null
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

    const handleSseData = (rawData: string) => {
      try {
        const parsed = JSON.parse(rawData) as SSEEvent<T>
        if (parsed.type === 'ready') {
          setStatus('open')
          reconnectAttemptsRef.current = 0
          return
        }
        if (typeof parsed.sequence === 'number' && parsed.sequence > lastEventIdRef.current) {
          lastEventIdRef.current = parsed.sequence
        }
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
        console.warn('[useTaskEvents] 解析 SSE 帧失败:', err, rawData)
      }
    }

    const connect = async () => {
      if (closedByUnmountRef.current) return
      const token = localStorage.getItem('crucible_token')
      if (!token) {
        setStatus('closed')
        errorRef.current = '登录已过期，请重新登录'
        setError(errorRef.current)
        return
      }

      const controller = new AbortController()
      abortControllerRef.current = controller

      const urlString = buildTaskEventStreamUrl({
        origin: window.location.origin,
        taskId,
        lastEventId: lastEventIdRef.current,
      })

      try {
        const response = await fetch(urlString, {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: 'text/event-stream',
            ...(lastEventIdRef.current > 0 ? { 'Last-Event-ID': String(lastEventIdRef.current) } : {}),
          },
          signal: controller.signal,
        })

        if (!response.ok) {
          if (response.status === 401) {
            try {
              await api.me()
            } catch (authErr) {
              if (isUnauthorizedError(authErr)) {
                setStatus('closed')
                errorRef.current = '登录已过期，请重新登录'
                setError(errorRef.current)
                return
              }
            }
          }
          if (response.status === 404) {
            setStatus('closed')
            errorRef.current = '任务不存在'
            setError(errorRef.current)
            return
          }
          throw new Error(`HTTP ${response.status}: ${response.statusText}`)
        }

        setStatus('open')
        reconnectAttemptsRef.current = 0

        const reader = response.body?.getReader()
        if (!reader) {
          throw new Error('ReadableStream not supported')
        }

        const decoder = new TextDecoder()
        let buffer = ''

        while (!controller.signal.aborted) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split(/\r?\n/)
          buffer = lines.pop() ?? ''

          for (const line of lines) {
            const trimmed = line.trim()
            if (!trimmed || trimmed.startsWith(':')) {
              continue
            }
            if (trimmed.startsWith('data:')) {
              const dataPayload = trimmed.slice(5).trim()
              if (dataPayload) {
                handleSseData(dataPayload)
              }
            }
          }
        }
      } catch (err: unknown) {
        if (controller.signal.aborted || closedByUnmountRef.current) return
        console.warn('[useTaskEvents] SSE 连接中断:', err)
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
        void connect()
      }, delay)
    }

    void connect()

    return () => {
      closedByUnmountRef.current = true
      cleanup()
      setStatus('closed')
    }
  }, [taskId, enabled, maxEvents, maxReconnectDelay, flushIntervalMs])

  return { events, status, error, reset }
}