/** 操作失败、请求失败用 antd message toast；同一文案在恢复前不重复弹。 */

export function errorToastText(error: unknown, fallback: string): string {
  if (typeof error === 'string' && error.trim()) return error.trim()
  if (error instanceof Error && error.message.trim()) return error.message.trim()
  return fallback
}

export function nextErrorToast(
  lastText: string | null,
  isError: boolean,
  error: unknown,
  fallback: string,
): { lastText: string | null; toast: string | null } {
  if (!isError) return { lastText: null, toast: null }
  const text = errorToastText(error, fallback)
  if (lastText === text) return { lastText, toast: null }
  return { lastText: text, toast: text }
}
