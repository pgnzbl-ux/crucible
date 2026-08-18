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
    return [creds.username && `账号 ${creds.username}`, creds.password && `密码 ${creds.password}`]
      .filter(Boolean)
      .join(' / ')
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
      const origin = str(o.origin) === 'minio' ? 'MinIO 缓存' : str(o.origin) === 'git' ? 'Git clone' : ''
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
    const origin = str(o.origin) === 'minio' ? 'MinIO' : str(o.origin) === 'git' ? 'Git' : ''
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

/** 查询未返回才算加载中；`[]` 是新 run 尚未建 NodeRun，应画 6 个 pending。 */
export function isNodeListLoading(nodes: unknown[] | undefined): nodes is undefined {
  return nodes === undefined
}

export const NODE_TERMINAL_STATUSES = ['completed', 'failed', 'skipped', 'cancelled'] as const

export function isNodeTerminal(status: string): boolean {
  return (NODE_TERMINAL_STATUSES as readonly string[]).includes(status)
}

/** pending 还没发生，点了只会看到空事件流。 */
export function isNodeSelectable(status: string): boolean {
  return status !== 'pending'
}

/** 任务已取消时，仍显示 running/pending 的节点按已取消展示。 */
export function displayNodeStatus(nodeStatus: string, taskStatus?: string): string {
  if (taskStatus === 'cancelled' && (nodeStatus === 'running' || nodeStatus === 'pending')) {
    return 'cancelled'
  }
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
}

function ssePayload(event: unknown): Record<string, unknown> {
  if (event && typeof event === 'object' && !Array.isArray(event)) {
    return event as Record<string, unknown>
  }
  return {}
}

/** 把 SSE 叠到步骤条：node.updated 改状态，env_ready 的 phase.updated 改进度句。 */
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
    if (ev.type === 'phase.updated') {
      const phase = typeof p.phase === 'string' ? p.phase : ''
      const msg = str(p.message)
      if (phase !== 'env_ready' || !msg) continue
      const prev = map.get('env_ready') ?? {}
      map.set('env_ready', {
        ...prev,
        output: { ...(prev.output ?? {}), progress: msg },
      })
    }
  }
  return map
}
