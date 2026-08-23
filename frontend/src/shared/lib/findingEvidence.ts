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
