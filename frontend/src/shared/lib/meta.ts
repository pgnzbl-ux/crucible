import type { TagProps } from 'antd'

// 任务状态 → 展示配置
export const TASK_STATUS_META: Record<string, { label: string; color: TagProps['color'] }> = {
  pending: { label: '待处理', color: 'default' },
  queued: { label: '排队中', color: 'blue' },
  running: { label: '分析中', color: 'processing' },
  needs_review: { label: '待复核', color: 'orange' },
  completed: { label: '已完成', color: 'green' },
  failed: { label: '失败', color: 'red' },
  cancelled: { label: '已取消', color: 'default' },
}

// 优先级 → 展示配置
export const PRIORITY_META: Record<string, { label: string; color: TagProps['color'] }> = {
  low: { label: '低', color: 'default' },
  medium: { label: '中', color: 'blue' },
  high: { label: '高', color: 'orange' },
  critical: { label: '严重', color: 'red' },
}

// 结论 → 展示配置
export const CONCLUSION_META: Record<string, { label: string; color: TagProps['color'] }> = {
  exists: { label: '漏洞确认存在', color: 'red' },
  not_exists: { label: '不存在（误报）', color: 'green' },
  unconfirmed: { label: '无法确认', color: 'orange' },
  failed: { label: '分析失败', color: 'default' },
}

export const EVENT_PHASE_LABELS: Record<string, string> = {
  start: '启动分析',
  preflight: '环境准备',
  running: '执行分析',
  scanning: '代码审计',
  reproducing: '尝试复现',
  completed: '完成',
  failed: '失败',
}

export function getStatusMeta(status: string) {
  return TASK_STATUS_META[status] ?? { label: status, color: 'default' as const }
}

export function getPriorityMeta(priority: string) {
  return PRIORITY_META[priority] ?? { label: priority, color: 'default' as const }
}

export function getConclusionMeta(conclusion: string) {
  return CONCLUSION_META[conclusion] ?? { label: conclusion, color: 'default' as const }
}
