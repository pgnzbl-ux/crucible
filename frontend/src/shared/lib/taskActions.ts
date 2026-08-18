export const ACTIVE_STATUSES = ['pending', 'queued', 'running'] as const
export const RETRY_STATUSES = ['failed', 'cancelled', 'completed', 'needs_review'] as const
export const BLOCK_DELETE_STATUSES = ['running', 'pending', 'queued', 'archived'] as const
/** 单节点重试允许的起点（与后端 _RETRYABLE_FROM_NODES 对齐）。source/profile 走整条重试。 */
export const RETRYABLE_FROM_NODES = ['env_ready', 'audit', 'reproduce', 'report'] as const

export type TaskDetailTab = 'overview' | 'progress' | 'events' | 'report'

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

export function defaultTaskDetailTab(_status?: string): TaskDetailTab {
  return 'progress'
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
    title: '删除任务',
    content: '删除后任务将从列表中移除，关联报告一并不可见。确定继续？',
    okText: '删除',
  },
} as const
