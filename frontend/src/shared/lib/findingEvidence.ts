function firstText(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number') return String(value)
  }
  return ''
}

function formatFlowStep(step: unknown): string {
  if (typeof step === 'string') return step.trim()
  if (typeof step === 'number' || typeof step === 'boolean') return String(step)
  if (!step || typeof step !== 'object' || Array.isArray(step)) return ''

  const record = step as Record<string, unknown>
  const file = firstText(record, ['file', 'path', 'file_path', 'uri'])
  const line = firstText(record, ['line', 'line_number', 'start_line'])
  const detail = firstText(record, ['expression', 'label', 'message', 'name', 'content'])
  const locus = file ? `${file}${line ? `:${line}` : ''}` : ''
  if (locus && detail) return `${locus} (${detail})`
  if (locus || detail) return locus || detail

  try {
    return JSON.stringify(record)
  } catch {
    return String(step)
  }
}

export function formatSourceToSink(steps: readonly unknown[] | null | undefined): string {
  return (steps ?? []).map(formatFlowStep).filter(Boolean).join(' → ')
}

export type EvidenceMeta = {
  hasDataflow: boolean
  ruleClass: string | null
  confidence: string | null
}

/** 从代表 Finding.raw + source_to_sink 提取证据元数据（非引擎结论措辞）。 */
export function evidenceMetaFromFinding(finding: {
  source_to_sink?: unknown[] | null
  raw?: Record<string, unknown> | null
} | null | undefined): EvidenceMeta {
  const raw = finding?.raw && typeof finding.raw === 'object' ? finding.raw : {}
  const hasFlowFromRaw = raw.has_dataflow === true
  const hasFlowFromSteps = Array.isArray(finding?.source_to_sink) && finding.source_to_sink.length > 0
  const ruleClass = typeof raw.rule_class === 'string' && raw.rule_class.trim()
    ? raw.rule_class.trim().toLowerCase()
    : null
  const confidence = typeof raw.confidence === 'string' && raw.confidence.trim()
    ? raw.confidence.trim().toUpperCase()
    : null
  return {
    hasDataflow: hasFlowFromRaw || hasFlowFromSteps,
    ruleClass,
    confidence,
  }
}

export function ruleClassLabel(ruleClass: string | null): string | null {
  if (ruleClass === 'known') return '已知厂商规则'
  if (ruleClass === 'generic') return '泛匹配/熵规则'
  return null
}

export function displaySourcePath(path: string | null | undefined): string {
  if (!path) return '-'
  const normalized = path.replace(/\\/g, '/')
  const parts = normalized.split('/').filter(Boolean)
  if (parts.length <= 3) return parts.join('/') || path
  return parts.slice(-3).join('/')
}

export function isRedactedSecret(text: string | null | undefined): boolean {
  if (!text) return false
  const trimmed = text.trim()
  if (!trimmed) return false
  if (/^\[?REDACTED\]?$/i.test(trimmed)) return true
  return trimmed.split('\n').every((line) => /^\[?REDACTED\]?$/i.test(line.trim()) || !line.trim())
}

function asRecord(raw: unknown): Record<string, unknown> {
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as Record<string, unknown> : {}
}

function strVal(raw: Record<string, unknown>, key: string): string {
  const value = raw[key]
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

function strList(raw: Record<string, unknown>, key: string): string[] {
  const value = raw[key]
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item).trim()).filter(Boolean)
}

function severityZh(severity: string | null | undefined, score?: number | null): string {
  if (typeof score === 'number' && Number.isFinite(score)) {
    if (score >= 9) return '严重'
    if (score >= 7) return '高危'
    if (score >= 4) return '中危'
    if (score > 0) return '低危'
  }
  const normalized = (severity ?? '').toLowerCase()
  if (normalized === 'critical') return '严重'
  if (normalized === 'high' || normalized === 'error') return '高危'
  if (normalized === 'medium' || normalized === 'warning') return '中危'
  if (normalized === 'low' || normalized === 'note') return '低危'
  if (normalized === 'info') return '提示'
  return ''
}

function calledLabel(value: unknown): string {
  if (value === true) return '受影响代码已被调用'
  if (value === false) return '受影响代码未被调用'
  return ''
}

export type EvidenceField = { label: string; value: string }
export type EvidenceLink = { label: string; href: string }

export type FindingEvidenceView = {
  cardTitle: string
  fields: EvidenceField[]
  body: string | null
  bodyKind: 'code' | 'advisory'
  redacted: boolean
  links: EvidenceLink[]
}

export function findingEvidenceView(finding: {
  engine: string
  rule_id: string
  message: string
  file_path: string
  line_start?: number | null
  line_end?: number | null
  severity?: string | null
  code_snippet?: string | null
  raw?: Record<string, unknown> | null
}): FindingEvidenceView {
  const raw = asRecord(finding.raw)
  const engine = finding.engine
  const snippet = (finding.code_snippet || '').trim()
  const message = (finding.message || '').trim()
  const locus = finding.line_start
    ? `${displaySourcePath(finding.file_path)}:${finding.line_start}`
    : displaySourcePath(finding.file_path)

  if (engine === 'osv') {
    const dep = [strVal(raw, 'dependency_name'), strVal(raw, 'version')].filter(Boolean).join(' ')
    const ecosystem = strVal(raw, 'ecosystem')
    const cve = strVal(raw, 'cve')
    const aliases = strList(raw, 'aliases').filter((item) => item !== finding.rule_id && item !== cve)
    const scoreRaw = raw.cvss_score
    const score = typeof scoreRaw === 'number' ? scoreRaw
      : typeof raw.cvss === 'number' ? raw.cvss
        : null
    const label = strVal(raw, 'severity_label') || severityZh(finding.severity, score)
    const fixed = strList(raw, 'fixed_versions')
    const fields: EvidenceField[] = []
    if (dep) fields.push({ label: '依赖', value: ecosystem ? `${dep}（${ecosystem}）` : dep })
    if (locus && locus !== '-') fields.push({ label: '清单', value: locus })
    fields.push({
      label: '漏洞编号',
      value: [finding.rule_id, cve].filter(Boolean).join(' / '),
    })
    if (aliases.length) fields.push({ label: '别名', value: aliases.slice(0, 8).join('、') })
    if (label) {
      fields.push({
        label: '严重度',
        value: score != null ? `${label}（CVSS ${score}）` : label,
      })
    }
    const reachable = calledLabel(raw.called)
    if (reachable) fields.push({ label: '可达性', value: reachable })
    if (fixed.length) fields.push({ label: '修复版本', value: fixed.join('、') })
    const summary = strVal(raw, 'summary')
    const details = strVal(raw, 'details')
    const body = details || summary || (fields.length ? null : (snippet || message))
    const osvUrl = strVal(raw, 'osv_url') || (finding.rule_id
      ? `https://osv.dev/vulnerability/${finding.rule_id}`
      : '')
    return {
      cardTitle: '依赖漏洞详情',
      fields,
      body: body || null,
      bodyKind: 'advisory',
      redacted: false,
      links: osvUrl ? [{ label: '在 osv.dev 查看公告', href: osvUrl }] : [],
    }
  }

  if (engine === 'gitleaks') {
    const description = strVal(raw, 'description') || finding.rule_id
    const fields: EvidenceField[] = [
      { label: '规则', value: description },
    ]
    const klass = ruleClassLabel(strVal(raw, 'rule_class'))
    if (klass) fields.push({ label: '类型', value: klass })
    if (locus && locus !== '-') fields.push({ label: '位置', value: locus })
    const entropy = raw.entropy
    if (typeof entropy === 'number' && Number.isFinite(entropy)) {
      fields.push({ label: '熵', value: String(entropy) })
    }
    const commitBits = [strVal(raw, 'commit'), strVal(raw, 'author'), strVal(raw, 'date')].filter(Boolean)
    if (commitBits.length) fields.push({ label: '提交', value: commitBits.join(' · ') })
    const body = snippet || message
    return {
      cardTitle: '泄露详情',
      fields,
      body: body || null,
      bodyKind: 'code',
      redacted: isRedactedSecret(body),
      links: [],
    }
  }

  return {
    cardTitle: '命中代码',
    fields: locus && locus !== '-' ? [{ label: '位置', value: locus }] : [],
    body: snippet || message || null,
    bodyKind: 'code',
    redacted: false,
    links: [],
  }
}
