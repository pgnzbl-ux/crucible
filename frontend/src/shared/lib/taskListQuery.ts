export const DEFAULT_PAGE_SIZE = 20

export interface TaskListQueryInput {
  status?: string
  priority?: string
  q?: string
  dateFrom?: string
  dateTo?: string
  page?: number
  pageSize?: number
}

export function buildTaskListApiParams(params: TaskListQueryInput): Record<string, string> {
  const page = params.page && params.page > 0 ? params.page : 1
  const pageSize = params.pageSize && params.pageSize > 0 ? params.pageSize : DEFAULT_PAGE_SIZE
  const api: Record<string, string> = {
    limit: String(pageSize),
    offset: String((page - 1) * pageSize),
  }
  if (params.status) api.status = params.status
  if (params.priority) api.priority = params.priority
  if (params.q) api.q = params.q
  if (params.dateFrom) api.date_from = params.dateFrom
  if (params.dateTo) api.date_to = params.dateTo
  return api
}

const ACTIVE = new Set(['pending', 'queued', 'running'])

/** 当前页有进行中任务则 5s；终态筛选不轮询；其余 30s 兜底。 */
export function taskListPollMs(
  items: Array<{ status: string }>,
  statusFilter?: string,
): number | false {
  const filters = statusFilter
    ? statusFilter.split(',').map((s) => s.trim()).filter(Boolean)
    : []
  if (filters.length > 0 && filters.every((s) => !ACTIVE.has(s))) {
    return false
  }
  if (items.some((item) => ACTIVE.has(item.status))) return 5_000
  return 30_000
}

/** 工作台：有进行中任务 5s，已有数据则 30s，尚未拉到 stats 时 15s。 */
export function statsPollMs(byStatus?: Record<string, number>): number {
  if (!byStatus) return 15_000
  if ([...ACTIVE].some((status) => (byStatus[status] ?? 0) > 0)) return 5_000
  return 30_000
}

export function sumTaskStats(byStatus: Record<string, number>, statusFilter?: string): number {
  if (!statusFilter) {
    return Object.values(byStatus).reduce((sum, n) => sum + n, 0)
  }
  return statusFilter.split(',').reduce((sum, key) => sum + (byStatus[key.trim()] ?? 0), 0)
}
