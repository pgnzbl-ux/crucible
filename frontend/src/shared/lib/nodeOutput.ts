/** 把节点 output 收成进度条上一句人话，方便观测过程。 */

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
