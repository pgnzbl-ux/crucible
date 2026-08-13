import { useCallback, useMemo } from 'react'
import { useLocation } from 'wouter'

export interface TaskListParams {
  status?: string
  priority?: string
  q?: string
  dateFrom?: string
  dateTo?: string
}

function parseParams(search: string): TaskListParams {
  const sp = new URLSearchParams(search)
  return {
    status: sp.get('status') ?? undefined,
    priority: sp.get('priority') ?? undefined,
    q: sp.get('q') ?? undefined,
    dateFrom: sp.get('dateFrom') ?? undefined,
    dateTo: sp.get('dateTo') ?? undefined,
  }
}

function buildSearch(params: TaskListParams): string {
  const sp = new URLSearchParams()
  if (params.status) sp.set('status', params.status)
  if (params.priority) sp.set('priority', params.priority)
  if (params.q) sp.set('q', params.q)
  if (params.dateFrom) sp.set('dateFrom', params.dateFrom)
  if (params.dateTo) sp.set('dateTo', params.dateTo)
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export function useTaskListParams() {
  const [location, navigate] = useLocation()

  const params = useMemo(() => {
    const qIdx = location.indexOf('?')
    const search = qIdx >= 0 ? location.slice(qIdx) : ''
    return parseParams(search)
  }, [location])

  const setParams = useCallback(
    (next: Partial<TaskListParams>) => {
      const merged: TaskListParams = { ...params }
      for (const [key, value] of Object.entries(next)) {
        const k = key as keyof TaskListParams
        if (value === undefined || value === '') {
          delete merged[k]
        } else {
          merged[k] = value
        }
      }
      navigate(`/tasks${buildSearch(merged)}`)
    },
    [params, navigate],
  )

  const clearParams = useCallback(() => navigate('/tasks'), [navigate])

  return { params, setParams, clearParams }
}
