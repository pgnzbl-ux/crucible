export const ACTIVE_STATUSES = ['pending', 'queued', 'running'] as const
export const RETRY_STATUSES = ['failed', 'cancelled', 'completed', 'needs_review'] as const
export const BLOCK_DELETE_STATUSES = ['running', 'pending', 'queued', 'archived'] as const
/** 单节点重试允许的起点（与后端 _RETRYABLE_FROM_NODES 对齐）。source/profile 走整条重试。 */
export const RETRYABLE_FROM_NODES = [
  'scan_gitleaks',
  'scan_osv',
  'scan_semgrep',
  'env_ready',
  'cluster',
  'screen',
  'triage',
  'dispatch',
  'audit',
  'reproduce',
  'report',
] as const

export type TaskDetailTab = 'overview' | 'progress' | 'report'

export function taskDetailTabFromValue(value: string | null | undefined): TaskDetailTab | null {
  // 兼容合并前的运行日志链接：原 events 页现在归入审计过程工作台。
  if (value === 'events') return 'progress'
  if (value === 'overview' || value === 'progress' || value === 'report') return value
  return null
}

export function canCancel(status: string): boolean {
  return (ACTIVE_STATUSES as readonly string[]).includes(status)
}

export function canRetry(status: string): boolean {
  return (RETRY_STATUSES as readonly string[]).includes(status)
}

export function canRetryFromNode(taskStatus: string, nodeKey: string, nodeStatus: string): boolean {
  return (
    canRetry(taskStatus)
    && nodeStatus === 'failed'
    && (RETRYABLE_FROM_NODES as readonly string[]).includes(nodeKey)
  )
}

export function canDelete(status: string): boolean {
  return !(BLOCK_DELETE_STATUSES as readonly string[]).includes(status)
}

export function defaultTaskDetailTab(status?: string): TaskDetailTab {
  return status && (ACTIVE_STATUSES as readonly string[]).includes(status) ? 'progress' : 'overview'
}

/** 仅终态且可能已出报告时才拉报告，避免失败任务刷 404。 */
export function shouldFetchTaskReport(status: string): boolean {
  return status === 'completed' || status === 'needs_review'
}

export function reportBelongsToCurrentRun(
  report: { run_id: string } | null | undefined,
  runId: string | null | undefined,
): boolean {
  return !!report && !!runId && report.run_id === runId
}

export const CONFIRM_COPY = {
  cancel: {
    title: '取消任务',
    content: '取消后将停止沙箱中正在运行的 Agent。确定继续？',
    okText: '确定取消',
  },
  retry: {
    title: '重试任务',
    content: '将从源码获取开始整条重跑，不沿用上次进度。确定继续？',
    okText: '重试',
  },
  delete: {
    title: '归档审计运行',
    content: '归档后将从默认列表中移除，关联报告仍保留，可通过“已归档”筛选查看。确定继续？',
    okText: '归档',
  },
} as const
