const REF_TYPE_LABELS: Record<string, string> = {
  branch: '分支',
  tag: '标签',
  commit: '提交',
  upload: '上传版本',
}

const VERDICT_LABELS: Record<string, string> = {
  confirmed: '已确认漏洞',
  partial: '部分确认',
  code_reachable: '代码路径可达',
  code_smell: '发现风险代码',
  false_positive: '已排除误报',
  not_reproduced: '动态复现未成功',
  needs_review: '需要人工复核',
}

export function projectLabel(address: string | null | undefined): string {
  if (!address) return '未知项目'
  if (address.startsWith('upload://')) {
    return address.split('/').filter(Boolean).at(-1) || '上传源码'
  }
  const cleaned = address.replace(/^https?:\/\//, '').replace(/\.git$/, '').replace(/\/$/, '')
  const parts = cleaned.split('/').filter(Boolean)
  return parts.length >= 3 ? `${parts.at(-2)} / ${parts.at(-1)}` : parts.at(-1) || cleaned
}

export function sourceVersionLabel(ref: string | null | undefined, type: string | null | undefined): string {
  if (!ref) return '默认版本 HEAD'
  return `${REF_TYPE_LABELS[type ?? ''] ?? '版本'} ${ref}`
}

export function auditResultLabel(status: string, verdict: string | null | undefined): string {
  if (verdict) return VERDICT_LABELS[verdict] ?? verdict
  if (status === 'needs_review') return '需要人工复核'
  if (status === 'completed') return '暂未确认漏洞'
  if (status === 'failed') return '分析未完成'
  if (status === 'cancelled') return '分析已取消'
  if (status === 'archived') return '已归档'
  return '等待分析完成'
}

export function findingStatusLabel(status: string, resolution: string | null | undefined): string {
  if (resolution === 'confirmed') return '已确认漏洞'
  if (resolution === 'false_positive') return '已排除误报'
  if (resolution === 'ignored') return '已忽略'
  const labels: Record<string, string> = {
    new: '等待聚类',
    clustered: '等待 AI 研判',
    adjudicated: 'AI 研判完成',
    needs_review: '等待人工复核',
    dispatched: '漏洞终认中',
    resolved: '已处置',
  }
  return labels[status] ?? status
}

export function screeningStatusMeta(status: string): { label: string; color: string } {
  const meta: Record<string, { label: string; color: string }> = {
    retained: { label: '重点', color: 'red' },
    confirmed: { label: '已确认', color: 'success' },
    review: { label: '需复核', color: 'warning' },
    processing: { label: '初筛中', color: 'processing' },
    suppressed: { label: '已降噪', color: 'default' },
  }
  return meta[status] ?? { label: '待判断', color: 'default' }
}

export function reportTypeLabel(documentKind: string | null | undefined, taskType: string | null | undefined): string {
  if (documentKind === 'code_audit_report' || taskType === 'discovery') return '代码审计报告'
  return '定向验证记录'
}

export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null) return '大小未知'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
