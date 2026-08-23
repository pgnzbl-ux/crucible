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
