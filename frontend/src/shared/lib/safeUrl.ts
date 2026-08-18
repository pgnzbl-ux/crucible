const HTTP_PROTOCOLS = new Set(['http:', 'https:'])

/** Agent / 预签名外链只允许 http(s)。javascript:、data:、协议相对 URL 一律丢掉。 */
export function safeHttpUrl(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  if (!trimmed || trimmed.startsWith('//')) return null
  try {
    const url = new URL(trimmed)
    if (!HTTP_PROTOCOLS.has(url.protocol)) return null
    return url.href
  } catch {
    return null
  }
}

/** react-markdown urlTransform：相对路径保留，其余只放行 http(s)。 */
export function markdownUrlTransform(value: string): string {
  const trimmed = value.trim()
  if (!trimmed || trimmed.startsWith('//')) return ''
  if (
    trimmed.startsWith('#') ||
    trimmed.startsWith('/') ||
    trimmed.startsWith('./') ||
    trimmed.startsWith('../')
  ) {
    return trimmed
  }
  if (!/^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(trimmed)) return trimmed
  return safeHttpUrl(trimmed) ?? ''
}
