export const FINDING_SCOPE_VALUES = ['workbench', 'verifying', 'confirmed', 'reachable', 'all'] as const
export type FindingScope = (typeof FINDING_SCOPE_VALUES)[number]

/** 对人可见的处理进度：流水线状态或结案结果（互斥） */
export type FindingProgressValue =
  | 'status:dispatched'
  | 'resolution:confirmed'
  | 'resolution:code_reachable'

export const FINDING_PROGRESS_OPTIONS: { value: FindingProgressValue; label: string }[] = [
  { value: 'status:dispatched', label: '验证中' },
  { value: 'resolution:confirmed', label: '已确认漏洞' },
  { value: 'resolution:code_reachable', label: '代码可达' },
]

const PROGRESS_SET = new Set(FINDING_PROGRESS_OPTIONS.map((o) => o.value))

export function parseFindingProgress(query: URLSearchParams): FindingProgressValue | undefined {
  const resolution = query.get('resolution')
  if (resolution) {
    const key = `resolution:${resolution}` as FindingProgressValue
    if (PROGRESS_SET.has(key)) return key
  }
  const status = query.get('status')
  if (status) {
    const key = `status:${status}` as FindingProgressValue
    if (PROGRESS_SET.has(key)) return key
    // 旧深链 status=resolved 无结案细分时仍可读，但不映射到下拉（避免误显「已处置」）
  }
  return undefined
}

export function progressToParams(progress: FindingProgressValue | undefined): {
  status?: string
  resolution?: string
} {
  if (!progress) return {}
  const [kind, value] = progress.split(':') as ['status' | 'resolution', string]
  return kind === 'resolution' ? { resolution: value } : { status: value }
}

export function parseFindingScope(query: URLSearchParams): FindingScope {
  const requested = query.get('scope')
  if (requested && (FINDING_SCOPE_VALUES as readonly string[]).includes(requested)) {
    return requested as FindingScope
  }
  // 工作台深链带 status / resolution 时进入「全部 + 该条件」，避免和队列语义打架
  if (query.get('status') || query.get('resolution')) return 'all'
  return 'workbench'
}

export function buildFindingsSearch(next: {
  scope: FindingScope
  status?: string
  resolution?: string
  q?: string
  engine?: string
  clueGrade?: string
  aiVerdict?: string
  page: number
}): string {
  const qs = new URLSearchParams()
  if (next.scope !== 'workbench') qs.set('scope', next.scope)
  if (next.resolution) qs.set('resolution', next.resolution)
  else if (next.status) qs.set('status', next.status)
  if (next.q) qs.set('q', next.q)
  if (next.engine) qs.set('engine', next.engine)
  if (next.clueGrade) qs.set('grade', next.clueGrade)
  if (next.aiVerdict) qs.set('verdict', next.aiVerdict)
  if (next.page > 1) qs.set('page', String(next.page))
  const raw = qs.toString()
  return raw ? `/findings?${raw}` : '/findings'
}
