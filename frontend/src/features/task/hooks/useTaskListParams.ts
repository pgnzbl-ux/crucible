import { useCallback, useMemo } from 'react'
import { useLocation, useSearch } from 'wouter'

import { DEFAULT_PAGE_SIZE } from '../../../shared/lib/taskListQuery'

export interface TaskListParams {
  status?: string
  priority?: string
  taskType?: 'verify' | 'discovery'
  q?: string
  dateFrom?: string
  dateTo?: string
  page?: number
  pageSize?: number
  create?: boolean
}

function parseParams(search: string): TaskListParams {
  const sp = new URLSearchParams(search)
  const pageRaw = sp.get('page')
  const sizeRaw = sp.get('pageSize')
  const page = pageRaw ? Number(pageRaw) : undefined
  const pageSize = sizeRaw ? Number(sizeRaw) : undefined
  return {
    status: sp.get('status') ?? undefined,
    priority: sp.get('priority') ?? undefined,
    taskType: (sp.get('taskType') as TaskListParams['taskType']) ?? undefined,
    q: sp.get('q') ?? undefined,
    dateFrom: sp.get('dateFrom') ?? undefined,
    dateTo: sp.get('dateTo') ?? undefined,
    page: page && page > 1 ? page : undefined,
    pageSize: pageSize && pageSize !== DEFAULT_PAGE_SIZE ? pageSize : undefined,
    create: sp.get('create') === '1' ? true : undefined,
  }
}

function buildSearch(params: TaskListParams): string {
  const sp = new URLSearchParams()
  if (params.status) sp.set('status', params.status)
  if (params.priority) sp.set('priority', params.priority)
  if (params.taskType) sp.set('taskType', params.taskType)
  if (params.q) sp.set('q', params.q)
  if (params.dateFrom) sp.set('dateFrom', params.dateFrom)
  if (params.dateTo) sp.set('dateTo', params.dateTo)
  if (params.page && params.page > 1) sp.set('page', String(params.page))
  if (params.pageSize && params.pageSize !== DEFAULT_PAGE_SIZE) sp.set('pageSize', String(params.pageSize))
  if (params.create) sp.set('create', '1')
  const s = sp.toString()
  return s ? `?${s}` : ''
}

const FILTER_KEYS: (keyof TaskListParams)[] = ['status', 'priority', 'taskType', 'q', 'dateFrom', 'dateTo']

export function useTaskListParams() {
  const [, navigate] = useLocation()
  const search = useSearch()

  const params = useMemo(() => parseParams(search), [search])

  const setParams = useCallback(
    (next: Partial<TaskListParams>) => {
      const merged: TaskListParams = { ...params }
      for (const [key, value] of Object.entries(next)) {
        const k = key as keyof TaskListParams
        if (value === undefined || value === '' || value === false) {
          delete merged[k]
        } else {
          ;(merged[k] as TaskListParams[typeof k]) = value as never
        }
      }
      const filterChanged = FILTER_KEYS.some((k) => k in next)
      if (filterChanged && !('page' in next)) {
        delete merged.page
      }
      navigate(`/tasks${buildSearch(merged)}`)
    },
    [params, navigate],
  )

  const clearParams = useCallback(() => navigate('/tasks'), [navigate])

  return { params, setParams, clearParams }
}
