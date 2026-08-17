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
  archived: { label: '已归档', color: 'default' },
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

// 判定(verdict,对齐后端 spec §1.4)。needs_review 是 audit uncertain 的验证记录判定。
export const VERDICT_META: Record<string, { label: string; color: TagProps['color'] }> = {
  confirmed: { label: '已确认', color: 'red' },
  partial: { label: '部分确认', color: 'orange' },
  code_reachable: { label: '代码可达', color: 'gold' },
  code_smell: { label: 'CODE SMELL', color: 'blue' },
  false_positive: { label: '误报', color: 'green' },
  not_reproduced: { label: '未复现', color: 'default' },
  needs_review: { label: '待复核', color: 'orange' },
}

// 白盒审计 Phase 2.5 Gate 三态(audit 节点 gate_verdict)
export const GATE_VERDICT_META: Record<string, { label: string; color: TagProps['color'] }> = {
  pass: { label: 'Gate 通过', color: 'red' },
  fail: { label: 'Gate 失败（误报）', color: 'green' },
  uncertain: { label: '待复核', color: 'orange' },
  unknown: { label: '结论缺失', color: 'default' },
}

export const REPORT_STATUS_META: Record<string, { label: string; color: TagProps['color'] }> = {
  draft: { label: '草稿', color: 'default' },
  generated: { label: '已生成', color: 'blue' },
  published: { label: '已发布', color: 'green' },
}

// 6 节点(node_key,对齐编排器)
export const NODE_LABELS: Record<string, string> = {
  source: '源码获取',
  profile: '项目画像',
  env_ready: '靶场就绪',
  audit: '白盒审计',
  reproduce: '复现验证',
  report: '报告生成',
}

export const NODE_STATUS_META: Record<string, { label: string; color: TagProps['color']; status: string }> = {
  pending: { label: '等待', color: 'default', status: 'wait' },
  running: { label: '执行中', color: 'processing', status: 'process' },
  completed: { label: '完成', color: 'success', status: 'finish' },
  failed: { label: '失败', color: 'error', status: 'error' },
  cancelled: { label: '已取消', color: 'default', status: 'wait' },
}

export const EVENT_PHASE_LABELS: Record<string, string> = {
  start: '启动分析',
  preflight: '环境准备',
  credentials: '凭据注入',
  running: '执行分析',
  env_ready: '靶场构建',
  scanning: '代码审计',
  reproducing: '尝试复现',
  completed: '完成',
  failed: '失败',
}

export const EVENT_TYPE_LABELS: Record<string, string> = {
  'agent.thinking': '思考',
  'agent.message': '回复',
  'agent.completed': '完成',
  'agent.failed': '失败',
  'tool.call.started': '工具开始',
  'tool.call.completed': '工具结束',
  'tool.call.denied': '工具拒绝',
  'node.updated': '节点',
  'phase.updated': '阶段',
  'raw.message': '原始消息',
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

export function getVerdictMeta(verdict: string) {
  return VERDICT_META[verdict] ?? { label: verdict, color: 'default' as const }
}

export function getReportStatusMeta(status: string) {
  return REPORT_STATUS_META[status] ?? { label: status, color: 'default' as const }
}

export function getGateVerdictMeta(verdict: string) {
  return GATE_VERDICT_META[verdict] ?? GATE_VERDICT_META.unknown
}
