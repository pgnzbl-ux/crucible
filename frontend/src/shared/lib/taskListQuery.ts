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
