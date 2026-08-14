function clearSession() {
  localStorage.removeItem('crucible_token')
  localStorage.removeItem('crucible_user')
}

async function authenticatedFetch(path: string): Promise<Response> {
  const token = localStorage.getItem('crucible_token')
  const res = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (res.status === 401) {
    clearSession()
    if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
      window.location.href = '/login'
    }
    throw new Error('登录已过期，请重新登录')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '请求失败' }))
    throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`)
  }
  return res
}

export async function downloadAuthenticated(path: string, filename: string): Promise<void> {
  const res = await authenticatedFetch(path)
  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  a.click()
  URL.revokeObjectURL(objectUrl)
}

export async function fetchAuthenticatedText(path: string): Promise<string> {
  const res = await authenticatedFetch(path)
  return res.text()
}
