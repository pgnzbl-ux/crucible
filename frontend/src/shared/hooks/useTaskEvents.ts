/**
 * useTaskEvents — 订阅任务实时事件流（SSE）
 *
 * 设计要点：
 * - 浏览器原生 EventSource，无需额外依赖
 * - 自动断线重连：onerror 后 delay 重连（指数退避 1s→2s→4s 上限 10s）
 * - 客户端组件卸载自动关闭
 * - token 通过 query 注入（EventSource 不支持自定义 header）
 *
 * 返回：
 * - events: 累积的全部事件数组（包含历史回放 + 实时推送）
 * - status: idle | connecting | open | reconnecting | closed
 * - error: 最近一次错误
 * - reset: 清空事件列表（用于任务切换时）
 */

import { useEffect, useRef, useState, useCallback } from 'react'

const API_BASE = '/api/v1'

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
}

export function useTaskEvents<T = unknown>(
  taskId: string | null,
  options: UseTaskEventsOptions = {},
) {
  const { enabled = true, maxReconnectDelay = 10_000 } = options
  const [events, setEvents] = useState<SSEEvent<T>[]>([])
  const [status, setStatus] = useState<SSEStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  const eventSourceRef = useRef<EventSource | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const reconnectTimerRef = useRef<number | null>(null)
  const closedByUnmountRef = useRef(false)

  const reset = useCallback(() => {
    setEvents([])
    setError(null)
  }, [])

  useEffect(() => {
    if (!enabled || !taskId) {
      setStatus('idle')
      return
    }

    closedByUnmountRef.current = false
    setStatus('connecting')

    const token = localStorage.getItem('crucible_token')
    const url = new URL(`${API_BASE}/tasks/${taskId}/events/stream`, window.location.origin)
    if (token) url.searchParams.set('token', token)
    const urlString = url.toString()

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
    }

    const connect = () => {
      if (closedByUnmountRef.current) return
      const newEs = new EventSource(urlString)
      es = newEs
      eventSourceRef.current = newEs

      newEs.onmessage = (e: MessageEvent) => {
        try {
          const parsed = JSON.parse(e.data) as SSEEvent<T>
          setEvents((prev) => {
            // 去重：sequence 一致的事件不重复（历史回放 + 实时推送短暂重叠）
            if (parsed.sequence != null) {
              const exists = prev.some((x) => x.sequence === parsed.sequence)
              if (exists) return prev
            }
            return [...prev, parsed]
          })
          setError(null)
        } catch (err) {
          console.warn('[useTaskEvents] 解析 SSE 帧失败:', err, e.data)
        }
      }

      newEs.addEventListener('ready', () => {
        setStatus('open')
        reconnectAttemptsRef.current = 0
      })

      newEs.onerror = () => {
        if (closedByUnmountRef.current) return
        if (newEs.readyState === EventSource.CLOSED) {
          const attempt = reconnectAttemptsRef.current + 1
          reconnectAttemptsRef.current = attempt
          const delay = Math.min(1000 * 2 ** (attempt - 1), maxReconnectDelay)
          setStatus('reconnecting')
          setError(`连接中断，${Math.round(delay / 1000)}s 后重连...`)
          reconnectTimerRef.current = window.setTimeout(() => {
            cleanup()
            connect()
          }, delay)
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, enabled])

  return { events, status, error, reset }
}