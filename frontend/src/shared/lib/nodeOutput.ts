/** 把节点 output 收成进度条上一句人话，方便观测过程。 */

import type { NodeRun } from './api'
import { VERIFY_MODE_SKIPPED_KEYS } from './meta'

export type NodeKey = 'source' | 'profile' | 'env_ready' | 'audit' | 'reproduce' | 'report'

const VERDICT_LABEL: Record<string, string> = {
  confirmed: '已确认',
  partial: '部分确认',
  code_reachable: '代码可达',
  code_smell: 'CODE SMELL',
  false_positive: '误报',
  not_reproduced: '未复现',
  needs_review: '待复核',
}

function str(v: unknown): string {
  return typeof v === 'string' ? v.trim() : ''
}

function sha7(v: unknown): string {
  const s = str(v)
  return s.length >= 7 ? s.slice(0, 7) : s
}

/**
 * 靶场凭据三态：拿到账密、Agent 确认免登录、以及"没给"。
 * 空对象不能当成免登录——那会把 Agent 漏挖说成靶场不需要登录。
 */
export type InitialCredsState = 'creds' | 'no_auth' | 'unknown'

export interface InitialCredsView {
  state: InitialCredsState
  username: string
  password: string
  loginUrl: string
  note: string
}

export function parseInitialCreds(raw: unknown): InitialCredsView {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { state: 'unknown', username: '', password: '', loginUrl: '', note: '' }
  }
  const c = raw as Record<string, unknown>
  const username = str(c.username) || str(c.user) || str(c.email)
  const password = str(c.password) || str(c.pass)
  const loginUrl = str(c.login_url) || str(c.loginUrl)
  const note = str(c.note) || str(c.hint)

  if (username || password) return { state: 'creds', username, password, loginUrl, note }
  if (c.auth_required === false) {
    return { state: 'no_auth', username: '', password: '', loginUrl, note }
  }
  return { state: 'unknown', username: '', password: '', loginUrl, note }
}

/** 摘要行只要短句，note 之类的长说明留给详情区。 */
function formatInitialCreds(raw: unknown): string {
  const creds = parseInitialCreds(raw)
  if (creds.state === 'creds') {
    return creds.username ? `已提供凭据 · ${creds.username}` : '已提供凭据'
  }
  return creds.state === 'no_auth' ? '免登录' : '无预设凭据'
}

export function summarizeNodeOutput(
  nodeKey: string,
  output: Record<string, unknown> | null | undefined,
  status?: string,
): string {
  if (status === 'running') return str(output?.progress) || '执行中'
  if (status === 'pending') return '等待'
  if (status === 'skipped') return '跳过'
  if (status === 'cancelled') return '已取消'
  if (status === 'failed') {
    const err = str(output?.error) || str(output?.detail) || str(output?.title)
    return err || '失败'
  }

  const o = output ?? {}
  switch (nodeKey as NodeKey) {
    case 'source': {
      const repo = str(o.repo_dirname) || str(o.project_key)
      const origin = str(o.origin) === 'minio' ? 'MinIO 缓存' : str(o.origin) === 'git' ? 'Git clone' : str(o.origin) === 'upload' ? '本地上传' : ''
      const ref = str(o.ref_name)
      const sha = sha7(o.commit_sha)
      const parts = [origin, repo, ref && `ref ${ref}`, sha && `@${sha}`].filter(Boolean)
      return parts.join(' · ') || '源码已就绪'
    }
    case 'profile': {
      const lang = str(o.language)
      const fw = str(o.framework)
      const stack = [lang, fw].filter(Boolean).join(' / ')
      const web = o.is_web === false ? '非 Web' : o.is_web === true ? 'Web' : ''
      const port = typeof o.port === 'number' ? `端口 ${o.port}` : str(o.port) ? `端口 ${str(o.port)}` : ''
      const services = Array.isArray(o.detected_services)
        ? o.detected_services.map((s) => str(s)).filter(Boolean).join(', ')
        : ''
      return [stack, web, port, services].filter(Boolean).join(' · ') || '画像完成'
    }
    case 'env_ready': {
      const url = str(o.target_url)
      if (o.outcome === 'degraded' || o.ok === false || (!url && o.error)) {
        return '靶场降级 · 白盒继续'
      }
      if (!url) return '靶场已就绪'
      return `${url} · ${formatInitialCreds(o.initial_creds)}`
    }
    case 'audit': {
      const gate = str(o.gate_verdict)
      if (gate === 'fail') return 'Gate 失败（误报）'
      if (gate === 'pass') {
        return o.runtime_dependent === true ? 'Gate 通过 · 运行时依赖' : 'Gate 通过'
      }
      if (gate === 'uncertain') return '待复核'
      return '审计完成'
    }
    case 'reproduce': {
      const v = str(o.verdict)
      return VERDICT_LABEL[v] || v || '复现完成'
    }
    case 'report': {
      const v = str(o.final_verdict)
      return VERDICT_LABEL[v] ? `报告已生成 · ${VERDICT_LABEL[v]}` : '报告已生成'
    }
    default:
      if (nodeKey === 'lead_verify') {
        const total = typeof o.lead_count === 'number' ? o.lead_count : null
        const done = typeof o.completed_count === 'number' ? o.completed_count : null
        const failed = typeof o.failed_count === 'number' ? o.failed_count : null
        if (status === 'running') {
          return total != null ? `正在终认 ${total} 条线索` : '正在逐条终认线索'
        }
        if (status === 'skipped') return '没有高置信线索，已跳过终认'
        const parts = [
          total != null ? `${total} 条线索` : '',
          done != null ? `完成 ${done}` : '',
          failed != null && failed > 0 ? `失败 ${failed}` : '',
        ].filter(Boolean)
        return parts.join(' · ') || '线索终认完成'
      }
      if (nodeKey === 'triage') {
        const adjudicated = typeof o.adjudicated_count === 'number' ? o.adjudicated_count : null
        const families = typeof o.family_count === 'number' ? o.family_count : null
        if (status === 'completed') {
          const parts = [
            adjudicated != null ? `已审 ${adjudicated}` : '',
            families != null ? `${families} 族` : '',
          ].filter(Boolean)
          return parts.join(' · ') || '二审完成'
        }
        return str(o.progress)
      }
      if (nodeKey === 'api_inventory') {
        if (o.skipped === true) return '已跳过'
        const count = typeof o.endpoint_count === 'number' ? o.endpoint_count : null
        const parsers = Array.isArray(o.parsers)
          ? o.parsers.map((p) => str(p)).filter(Boolean)
          : []
        const unsupported = Array.isArray(o.unsupported_languages)
          ? o.unsupported_languages.map((p) => str(p)).filter(Boolean)
          : []
        const parserLabel = parsers.join('/') || (str(o.parser) !== 'none' ? str(o.parser) : '')
        const parts = [
          count != null ? `${count} 端点` : '',
          parserLabel,
          unsupported.length ? `${unsupported.join('、')} 无 parser` : '',
        ].filter(Boolean)
        return parts.join(' · ') || '清单完成'
      }
      return status === 'completed' ? '完成' : ''
  }
}

const COMPACT_CAPTION_MAX = 22

function ellipsize(text: string, max = COMPACT_CAPTION_MAX): string {
  if (text.length <= max) return text
  return `${text.slice(0, max)}…`
}

/** 顶部钉住的横向步骤条只用短句，避免画像 summary 把布局撑成竖排长文。 */
export function compactNodeCaption(
  nodeKey: string,
  output: Record<string, unknown> | null | undefined,
  status?: string,
): string {
  if (status === 'running') return ellipsize(str(output?.progress) || '执行中')
  if (status === 'pending' || !status) return ''
  if (status === 'skipped') return '跳过'
  if (status === 'cancelled') return '已取消'
  if (status === 'failed') {
    return ellipsize(summarizeNodeOutput(nodeKey, output, status))
  }
  const o = output ?? {}
  if (nodeKey === 'profile') {
    const lang = str(o.language)
    const fw = str(o.framework)
    const stack = [lang, fw].filter(Boolean).join(' / ')
    const web = o.is_web === false ? '非 Web' : o.is_web === true ? 'Web' : ''
    return [stack, web].filter(Boolean).join(' · ') || '画像完成'
  }
  if (nodeKey === 'source') {
    const origin = str(o.origin) === 'minio' ? 'MinIO' : str(o.origin) === 'git' ? 'Git' : str(o.origin) === 'upload' ? '上传' : ''
    const repo = str(o.repo_dirname)
    return [origin, repo].filter(Boolean).join(' · ') || '源码已就绪'
  }
  if (nodeKey === 'env_ready') {
    return str(o.target_url) || '靶场已就绪'
  }
  if (nodeKey === 'audit') {
    const gate = str(o.gate_verdict)
    if (gate === 'fail') return 'Gate 失败'
    if (gate === 'pass') return o.runtime_dependent === true ? '运行时依赖' : 'Gate 通过'
    if (gate === 'uncertain') return '待复核'
    return '审计'
  }
  return ellipsize(summarizeNodeOutput(nodeKey, output, status))
}

/** 查询未返回才算加载中；`[]` 是新 run 尚未建 NodeRun。 */
export function isNodeListLoading(nodes: unknown[] | undefined): nodes is undefined {
  return nodes === undefined
}

export const NODE_TERMINAL_STATUSES = ['completed', 'failed', 'skipped', 'cancelled'] as const

export function isNodeTerminal(status: string): boolean {
  return (NODE_TERMINAL_STATUSES as readonly string[]).includes(status)
}

/** SSE 已连通时靠 node.updated 刷新；否则 3s 轮询。 */
export function nodeStepsPollMs(opts: {
  taskStatus?: string
  nodes?: Array<{ status: string }>
  sseLive?: boolean
}): number | false {
  if (opts.taskStatus === 'cancelled') return false
  const nodes = opts.nodes
  const taskTerminal = ['completed', 'failed', 'needs_review', 'cancelled', 'archived']
    .includes(opts.taskStatus ?? '')
  if (taskTerminal && nodes && nodes.every((n) => isNodeTerminal(n.status))) {
    return false
  }
  if (opts.sseLive) return false
  return 3000
}

/** pending 还没发生，点了只会看到空事件流。 */
export function isNodeSelectable(status: string): boolean {
  return status !== 'pending'
}

/**
 * 任务终止时只收敛仍在执行的节点；失败任务的 pending 节点从未启动，
 * 必须保留 pending，交给拓扑展示层标成“未执行”，不能伪造成 failed。
 */
export function displayNodeStatus(nodeStatus: string, taskStatus?: string): string {
  if (nodeStatus === 'running') {
    if (taskStatus === 'cancelled') return 'cancelled'
    if (taskStatus === 'failed') return 'failed'
  }
  if (nodeStatus === 'pending' && taskStatus === 'cancelled') return 'cancelled'
  return nodeStatus
}

/** REST 已是终态时，不用 SSE 里过期的 running 盖回去。 */
export function applyNodeOverlay(
  base: { status: string },
  overlay?: Partial<{ status: string }>,
): string {
  if (!overlay?.status) return base.status
  if (isNodeTerminal(base.status)) return base.status
  return overlay.status
}

export type NodeOverlayPatch = {
  status?: string
  output?: Record<string, unknown>
  usage?: {
    prompt_tokens: number
    completion_tokens: number
    cache_read_input_tokens: number
    cache_creation_input_tokens: number
    total_tokens: number
  }
}

function ssePayload(event: unknown): Record<string, unknown> {
  if (event && typeof event === 'object' && !Array.isArray(event)) {
    return event as Record<string, unknown>
  }
  return {}
}

function asUsage(raw: unknown): NodeOverlayPatch['usage'] | undefined {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined
  const o = raw as Record<string, unknown>
  const num = (k: string) => {
    const v = o[k]
    return typeof v === 'number' && Number.isFinite(v) ? v : 0
  }
  return {
    prompt_tokens: num('prompt_tokens'),
    completion_tokens: num('completion_tokens'),
    cache_read_input_tokens: num('cache_read_input_tokens'),
    cache_creation_input_tokens: num('cache_creation_input_tokens'),
    total_tokens: num('total_tokens'),
  }
}

/** 把 SSE 叠到步骤条：node.updated 改状态，env_ready 的 phase.updated 改进度句，usage.updated 叠用量。 */
export function overlayFromSseEvents(
  events: Array<{ type: string; event?: unknown }>,
): Map<string, NodeOverlayPatch> {
  const map = new Map<string, NodeOverlayPatch>()
  for (const ev of events) {
    const p = ssePayload(ev.event)
    if (ev.type === 'node.updated') {
      const key = typeof p.node_key === 'string' ? p.node_key : ''
      if (!key) continue
      const prev = map.get(key) ?? {}
      const status = typeof p.status === 'string' ? p.status : undefined
      const output = p.output && typeof p.output === 'object' && !Array.isArray(p.output)
        ? (p.output as Record<string, unknown>)
        : undefined
      map.set(key, {
        ...prev,
        ...(status ? { status } : {}),
        ...(output ? { output: { ...(prev.output ?? {}), ...output } } : {}),
      })
      continue
    }
    if (ev.type === 'usage.updated') {
      const key = typeof p.node_key === 'string' ? p.node_key : ''
      if (!key) continue
      const cumulative = asUsage(p.cumulative) ?? asUsage(p.usage)
      if (!cumulative) continue
      const prev = map.get(key) ?? {}
      map.set(key, { ...prev, usage: cumulative })
      continue
    }
    if (ev.type === 'phase.updated') {
      const phase = typeof p.phase === 'string' ? p.phase : ''
      const msg = str(p.message)
      // 步骤条 running 文案：画像 / 靶场 / 扫描阶段句写入 progress
      const progressPhases = new Set([
        'env_ready',
        'profile',
        'scan_semgrep',
        'scan_gitleaks',
        'scan_osv',
        'api_inventory',
        'cluster',
        'screen',
        'triage',
      ])
      if (!progressPhases.has(phase) || !msg) continue
      // 并发二审的「开始审议」只进事件流；左侧进度以 triage.progress / 完成句为准
      if (phase === 'triage' && msg.startsWith('开始审议')) continue
      const prev = map.get(phase) ?? {}
      map.set(phase, {
        ...prev,
        output: { ...(prev.output ?? {}), progress: msg },
      })
      continue
    }
    if (ev.type === 'triage.progress') {
      // 快审也会发 triage.progress（node_key=screen）；勿盖到 AI 二审上
      const key = typeof p.node_key === 'string' ? p.node_key : 'triage'
      if (key !== 'triage' && key !== '') continue
      const msg =
        str(p.message) ||
        triageProgressCaption(p)
      if (!msg) continue
      const prev = map.get('triage') ?? {}
      map.set('triage', {
        ...prev,
        output: { ...(prev.output ?? {}), progress: msg },
      })
    }
  }
  return map
}

function triageProgressCaption(p: Record<string, unknown>): string {
  const done = typeof p.done === 'number' ? p.done : null
  const total = typeof p.total === 'number' ? p.total : null
  const label = str(p.label)
  const familySize = typeof p.family_size === 'number' ? p.family_size : null
  if (done != null && total != null) {
    const note = familySize != null ? `（族内 ${familySize} 组）` : ''
    return label
      ? `二审 ${done}/${total}：${label}${note}`
      : `二审 ${done}/${total}`
  }
  const adjudicated = p.adjudicated
  const pending = p.pending
  if (adjudicated == null && pending == null) return ''
  const reason = str(p.reason) === 'budget' ? '（预算中断）' : ''
  return `已审 ${String(adjudicated ?? 0)}${pending != null ? `，待审 ${String(pending)}` : ''}${reason}`
}

// ---------------------------------------------------------------------------
// 节点耗时 / 指标 / 跳过原因 —— 审计过程列表与流程图悬停共用的展示数据
// ---------------------------------------------------------------------------

/**
 * 节点耗时：`45s` / `3m24s` / `1h05m`。运行中（finishedAt 为空）用当前时间，
 * 随列表轮询刷新；now 参数仅供测试注入。
 */
export function formatDuration(
  startedAt?: string | null,
  finishedAt?: string | null,
  now?: number,
): string {
  if (!startedAt) return ''
  const start = Date.parse(startedAt)
  if (Number.isNaN(start)) return ''
  const end = finishedAt ? Date.parse(finishedAt) : now ?? Date.now()
  if (Number.isNaN(end) || end < start) return ''
  const total = Math.floor((end - start) / 1000)
  if (total < 60) return `${total}s`
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m < 60) return s ? `${m}m${String(s).padStart(2, '0')}s` : `${m}m`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm ? `${h}h${String(rm).padStart(2, '0')}m` : `${h}h`
}

export interface NodeMetric {
  label: string
  value: string
}

export type NodeStatusOutputLike = Pick<NodeRun, 'node_key' | 'status' | 'output'>

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** 计数字典（groups_by_engine / candidate_state_counts）收成 `semgrep 12 · osv 3` 短句。 */
function countPairs(raw: unknown, limit = 3): string {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return ''
  return Object.entries(raw as Record<string, unknown>)
    .filter(([, v]) => typeof v === 'number' && v > 0)
    .sort((a, b) => (b[1] as number) - (a[1] as number))
    .slice(0, limit)
    .map(([k, v]) => `${k} ${v}`)
    .join(' · ')
}

/**
 * 已完成节点的关键产出指标（2–4 项）。audit / env_ready / lead_verify 有专用
 * 详情面板，不在此列；旧数据缺字段时自然回退到一行摘要。
 */
export function nodeMetrics(node: NodeStatusOutputLike): NodeMetric[] {
  if (node.status !== 'completed') return []
  const o = node.output ?? {}
  const key = node.node_key
  const metrics: NodeMetric[] = []
  const push = (label: string, value: string | number | null | undefined) => {
    if (value === null || value === undefined || value === '') return
    metrics.push({ label, value: String(value) })
  }
  const verdict = (v: unknown) => VERDICT_LABEL[str(v)] || str(v) || null

  if (key === 'source') {
    const origin = { minio: 'MinIO 缓存', git: 'Git clone', upload: '本地上传' }[str(o.origin)] || str(o.origin)
    const sha = sha7(o.commit_sha)
    push('提交', sha ? `@${sha}` : null)
    push('来源', origin)
    push('目录', str(o.repo_dirname) || str(o.project_key))
  } else if (key === 'profile') {
    push('语言', str(o.primary_language) || str(o.language))
    push('框架', str(o.framework) || (Array.isArray(o.frameworks) ? o.frameworks.map((f) => str(f)).filter(Boolean).join(' / ') : ''))
    if (o.is_web === true || o.is_web === false) push('Web', o.is_web ? '是' : '否')
    push('端口', num(o.port))
  } else if (key.startsWith('scan_')) {
    push('发现', num(o.finding_count))
    const outcome = str(o.outcome) || str(o.status)
    push('引擎状态', outcome === 'degraded' ? '降级' : outcome === 'success' ? '正常' : outcome || null)
  } else if (key === 'api_inventory') {
    push('端点', num(o.endpoint_count))
    push('PVE', num(o.pve_count))
    const parsers = Array.isArray(o.parsers) ? o.parsers.map((p) => str(p)).filter(Boolean) : []
    push('解析器', parsers.join(' / ') || str(o.parser) || null)
  } else if (key === 'api_hunt') {
    push('候选', num(o.candidate_count))
    push('状态分布', countPairs(o.candidate_state_counts))
    if (o.budget_exhausted === true) push('预算', '已耗尽')
  } else if (key === 'cluster') {
    push('组数', num(o.group_count))
    push('引擎分布', countPairs(o.groups_by_engine))
    push('符号索引', num(o.index_symbol_count))
    const dropped = num(o.dropped_c_count)
    if (dropped != null && dropped > 0) push('C 档丢弃', dropped)
  } else if (key === 'screen') {
    push('升级送审', num(o.escalated_count))
    push('真阳', num(o.tp_count))
    push('误报', num(o.fp_count))
    push('待定', num(o.need_more_count))
  } else if (key === 'triage') {
    push('已判定', num(o.adjudicated_count))
    push('真阳', num(o.tp_count))
    push('误报', num(o.fp_count))
    push('待定', num(o.need_more_count))
    const residual = num(o.skipped_unaudited_count)
    if (residual != null && residual > 0) push('未审残留', residual)
  } else if (key === 'dispatch') {
    push('入队线索', num(o.queued_count))
    if (typeof o.has_lead === 'boolean') push('合格线索', o.has_lead ? '有' : '无')
  } else if (key === 'reproduce') {
    push('结论', verdict(o.verdict))
    push('复现', o.reproduced === true ? '成功' : o.reproduced === false ? '未成功' : null)
    push('尝试', Array.isArray(o.attempts) ? o.attempts.length : null)
  } else if (key === 'finalize') {
    push('权威结论', verdict(o.analysis_verdict) || verdict(o.final_verdict))
    push('状态', o.analysis_status === 'needs_review' ? '待复核' : o.analysis_status === 'completed' ? '已定论' : str(o.analysis_status) || null)
    push('线索', num(o.lead_count))
    push('已确认', num(o.confirmed_count))
    const review = num(o.needs_review_count)
    if (review != null && review > 0) push('待复核线索', review)
  } else if (key === 'report') {
    push('文档结论', verdict(o.final_verdict))
  }
  return metrics
}

/**
 * 跳过原因：编排器 skip 只落状态不落原因（output 为空对象），这里按
 * 节点与任务模式推断；后端将来写入 skip_reason / skip_signal 时优先采用。
 */
export function skipReasonLabel(node: NodeStatusOutputLike, taskType?: 'verify' | 'discovery'): string {
  if (node.status !== 'skipped') return ''
  const raw = str(node.output?.skip_reason) || str(node.output?.skip_signal)
  if (raw) return raw
  const key = node.node_key
  if (taskType !== 'discovery' && VERIFY_MODE_SKIPPED_KEYS.has(key)) return '验证模式不执行'
  if (key === 'env_ready') return taskType === 'discovery' ? '非 Web 或无合格线索' : '非 Web 项目'
  if (key === 'lead_verify') return '无合格线索'
  if (key === 'reproduce') return 'Gate 未通过 / 非 Web'
  if (key === 'scan_semgrep') return '无适用语言扫描配置'
  return '按分支信号跳过'
}
