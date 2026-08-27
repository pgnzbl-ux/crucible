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

// 二审内部码 → 对用户文案（禁止把 tp/fp 当标签原文）
export const AI_VERDICT_META: Record<string, { label: string; color: TagProps['color'] }> = {
  tp: { label: '可疑真洞', color: 'red' },
  fp: { label: '误报', color: 'default' },
  need_more_context: { label: '二审未决', color: 'orange' },
  bypass: { label: '依赖情报', color: 'blue' },
}

export function getAiVerdictMeta(verdict: string | null | undefined): { label: string; color: TagProps['color'] } {
  if (!verdict) return { label: '尚未研判', color: 'default' }
  return AI_VERDICT_META[verdict] ?? { label: '未知结论', color: 'default' }
}

/** 线索发现引擎（AlertGroup.engine_set / RawFinding.engine） */
export const FINDING_ENGINE_LABELS: Record<string, string> = {
  semgrep: 'Semgrep 静态',
  gitleaks: 'Gitleaks 密钥',
  osv: 'OSV 依赖',
  api_hunt: 'API 鉴权猎洞',
}

export function formatFindingEngines(engines: string[] | null | undefined): string {
  if (!engines?.length) return '未知引擎'
  return engines.map((e) => FINDING_ENGINE_LABELS[e] ?? e).join(' + ')
}

// 判定(verdict,对齐 docs/discovery-spec.md §12)。needs_review 是 audit uncertain 的验证记录判定。
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

// 节点(node_key,对齐编排器)
export const NODE_LABELS: Record<string, string> = {
  source: '源码获取',
  profile: '项目画像',
  scan_gitleaks: '扫描·泄露',
  scan_osv: '扫描·依赖',
  scan_semgrep: '扫描·SAST',
  api_inventory: 'API 清单',
  env_ready: '靶场就绪',
  cluster: '聚类分组',
  api_hunt: 'API 猎洞',
  screen: '轻量快审',
  triage: 'AI 二审',
  dispatch: '线索调度',
  audit: '白盒审计',
  reproduce: '复现验证',
  lead_verify: '多线索终认',
  finalize: '结论固化',
  report: '报告文档',
  over: '结束',
}

// 执行顺序（就绪波次，与 DAG flowColumn 对齐）：与后端 catalog node_index 刻意不同——
// env_ready 的 catalog 索引是 6，但依赖 dispatch，实际在 dispatch 之后执行；
// api_hunt 的 catalog 索引是 8，但 cluster 依赖它，必须排在 cluster 之前。
export const PIPELINE_NODE_ORDER: string[] = [
  'source',
  'profile',
  'scan_gitleaks',
  'scan_osv',
  'scan_semgrep',
  'api_inventory',
  'api_hunt',
  'cluster',
  'screen',
  'triage',
  'dispatch',
  'env_ready',
  'audit',
  'reproduce',
  'lead_verify',
  'finalize',
  'report',
]

/** 调用过模型的节点（Agent 容器或快模型网关）；DAG / 阶段卡 AI 角标。
 * 与后端 `NodeExecutor.is_ai` 不完全等同：后者表示要起 Docker。
 * - screen：无 Docker，但 T2 快模型计费
 * - lead_verify：discovery 显式终认工位（per-lead audit/reproduce）
 * - finalize：固化 analysis_verdict（无 AI）
 * - report：verify 调模型；discovery 走代码聚合 */
export const AI_NODE_KEYS = new Set([
  'profile',
  'env_ready',
  'api_hunt',
  'screen',
  'triage',
  'audit',
  'reproduce',
  'report',
  'lead_verify',
])

export function isAiNode(nodeKey: string): boolean {
  return AI_NODE_KEYS.has(nodeKey)
}

export type TokenUsageParts = {
  prompt_tokens: number
  completion_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
  total_tokens: number
}

/** 合并多段台账用量（如 discovery 下 audit+reproduce → lead_verify）。 */
export function mergeTokenUsage(
  ...parts: Array<TokenUsageParts | null | undefined>
): TokenUsageParts | undefined {
  const list = parts.filter((p): p is TokenUsageParts => Boolean(p))
  if (list.length === 0) return undefined
  const sum = {
    prompt_tokens: 0,
    completion_tokens: 0,
    cache_read_input_tokens: 0,
    cache_creation_input_tokens: 0,
  }
  for (const u of list) {
    sum.prompt_tokens += u.prompt_tokens || 0
    sum.completion_tokens += u.completion_tokens || 0
    sum.cache_read_input_tokens += u.cache_read_input_tokens || 0
    sum.cache_creation_input_tokens += u.cache_creation_input_tokens || 0
  }
  return {
    ...sum,
    total_tokens:
      sum.prompt_tokens
      + sum.completion_tokens
      + sum.cache_read_input_tokens
      + sum.cache_creation_input_tokens,
  }
}

/** 紧凑展示 token 数（DAG 角标旁） */
export function formatTokenCount(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(Math.round(n))
}

// 验证任务会被 VERIFY_MODE 跳过的发现侧节点：步骤条默认隐藏
// 与 backend DEFAULT_PIPELINE skip_when=VERIFY_MODE 对齐（含清单 / 猎洞）
export const VERIFY_MODE_SKIPPED_KEYS = new Set([
  'scan_gitleaks',
  'scan_osv',
  'scan_semgrep',
  'api_inventory',
  'api_hunt',
  'cluster',
  'screen',
  'triage',
  'dispatch',
  'lead_verify',
])

export const NODE_STATUS_META: Record<string, { label: string; color: TagProps['color']; status: string }> = {
  pending: { label: '等待', color: 'default', status: 'wait' },
  running: { label: '执行中', color: 'processing', status: 'process' },
  completed: { label: '完成', color: 'success', status: 'finish' },
  failed: { label: '失败', color: 'error', status: 'error' },
  skipped: { label: '跳过', color: 'default', status: 'wait' },
}

export const EVENT_PHASE_LABELS: Record<string, string> = {
  start: '启动分析',
  preflight: '环境准备',
  credentials: '注入凭据',
  running: '执行分析',
  source: '源码获取',
  profile: '项目画像',
  scan_gitleaks: '扫描·泄露',
  scan_osv: '扫描·依赖',
  scan_semgrep: '扫描·SAST',
  env_ready: '靶场构建',
  api_inventory: 'API 清单',
  api_hunt: 'API 猎洞',
  cluster: '聚类分组',
  screen: '轻量快审',
  triage: 'AI 二审',
  dispatch: '线索调度',
  audit: '白盒审计',
  reproduce: '复现验证',
  lead_verify: '多线索终认',
  finalize: '结论固化',
  report: '报告文档',
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
  'agent.subagent.updated': '子代理',
  'tool.call.started': '工具开始',
  'tool.call.completed': '工具结束',
  'tool.call.denied': '工具拒绝',
  'node.updated': '节点',
  'phase.updated': '阶段',
  'triage.progress': '二审进度',
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
