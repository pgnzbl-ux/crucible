export const REPORT_SECTIONS = [
  { key: 'product_intro', label: '§1 产品介绍' },
  { key: 'vulnerability', label: '§2 漏洞描述' },
  { key: 'impact', label: '§3 影响范围' },
  { key: 'details', label: '§4 漏洞详情' },
  { key: 'reproduction', label: '§5 漏洞复现' },
  { key: 'poc_commands', label: '§6 POC' },
  { key: 'fix_suggestions', label: '§7 修复建议' },
  { key: 'reporting_decision', label: '§8 报送判定' },
] as const

export function asMarkdownSection(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed ? value : null
}

export function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value as Record<string, unknown>
}

export function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return []
  return value.filter(
    (item): item is Record<string, unknown> =>
      Boolean(item) && typeof item === 'object' && !Array.isArray(item),
  )
}

export function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string')
}
