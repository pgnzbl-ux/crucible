import { rejectIfNotOk } from './api'

async function authenticatedFetch(path: string): Promise<Response> {
  const token = localStorage.getItem('crucible_token')
  const res = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  await rejectIfNotOk(res, Boolean(token))
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
