/** 把节点 output 收成进度条上一句人话，方便观测过程。 */

export type NodeKey = 'source' | 'profile' | 'env_ready' | 'audit' | 'reproduce' | 'report'

const VERDICT_LABEL: Record<string, string> = {
  confirmed: '已确认',
  partial: '部分确认',
  code_reachable: '代码可达',
  code_smell: 'CODE SMELL',
  false_positive: '误报',
  not_reproduced: '未复现',
}

function str(v: unknown): string {
  return typeof v === 'string' ? v.trim() : ''
}

function sha7(v: unknown): string {
  const s = str(v)
  return s.length >= 7 ? s.slice(0, 7) : s
}

function formatInitialCreds(raw: unknown): string {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return ''
  const c = raw as Record<string, unknown>
  const user = str(c.username) || str(c.user) || str(c.email)
  const pass = str(c.password) || str(c.pass)
  const parts: string[] = []
  if (user) parts.push(`账号 ${user}`)
  if (pass) parts.push(`密码 ${pass}`)
  if (parts.length) return parts.join(' / ')
  const note = str(c.note) || str(c.hint)
  return note
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
      const creds = formatInitialCreds(o.initial_creds)
      return [url, creds].filter(Boolean).join(' · ') || '靶场已就绪'
    }
    case 'audit': {
      const gate = str(o.gate_verdict)
      if (gate === 'fail') return str(o.gate_reason) ? `Gate 失败 · ${str(o.gate_reason)}` : 'Gate 失败（误报）'
      if (gate === 'pass') {
        return o.runtime_dependent === true ? 'Gate 通过 · 运行时依赖' : 'Gate 通过'
      }
      if (gate === 'uncertain') return str(o.gate_reason) ? `待复核 · ${str(o.gate_reason)}` : '待复核'
      return str(o.kill_chain) || '审计完成'
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

/** 非 compact 的 audit 行：摘要 + 尚未出现在摘要里的 kill_chain。 */
export function formatAuditDetail(
  output: Record<string, unknown> | null | undefined,
  status?: string,
): string {
  const summary = summarizeNodeOutput('audit', output, status)
  const chain = str(output?.kill_chain)
  if ((status === 'completed' || !status) && chain && !summary.includes(chain)) {
    return `${summary}\n${chain}`
  }
  return summary
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
