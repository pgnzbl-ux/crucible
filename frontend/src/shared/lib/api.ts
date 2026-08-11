const API_BASE = '/api/v1'

// 401 处理：清 token + 跳登录（避免循环：登录页本身不触发）
function handleUnauthorized() {
  localStorage.removeItem('crucible_token')
  localStorage.removeItem('crucible_user')
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = localStorage.getItem('crucible_token')
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string>),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (res.status === 401) {
    handleUnauthorized()
    throw new Error('登录已过期，请重新登录')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '请求失败' }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  if (res.status === 204) {
    return undefined as T
  }
  return res.json()
}

// ── 类型 ──

// Auth
export interface CurrentUser {
  id: string
  email: string
  display_name: string
  is_active: boolean
  is_admin: boolean
  role: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  user: CurrentUser
}

export interface TaskSummary {
  id: string
  project_address: string
  status: string
  priority: string
  source_type: string
  owner_id: string
  created_at: string
  updated_at: string
}

export interface RunSummary {
  id: string
  task_id: string
  status: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  created_at: string
}

export interface TaskDetail extends TaskSummary {
  project_ref: string | null
  vulnerability_description: string
  vulnerability_reasoning: string | null
  credential_refs: string[]
  runs: RunSummary[]
}

export interface TaskListResponse {
  items: TaskSummary[]
  total: number
  limit: number
  offset: number
}

export interface AgentEvent {
  id: string
  run_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  source: string
  created_at: string
}

export interface ReportDetail {
  id: string
  task_id: string
  run_id: string
  status: string
  conclusion: string
  title: string
  summary: string | null
  reasoning: string | null
  evidence_summary: string | null
  artifact_key: string | null
  published_at: string | null
  created_at: string
  updated_at: string
  evidence: Evidence[]
}

export interface Evidence {
  id: string
  object_key: string
  bucket: string
  file_name: string
  content_type: string
  size_bytes: number
  kind: string
  created_at: string
  download_url: string | null
}

// ── LLM Provider ──

export interface LlmProvider {
  id: string
  name: string
  provider_type: string
  base_url: string
  api_key_masked: string
  has_api_key: boolean
  model: string
  timeout_ms: number
  enabled: boolean
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface LlmProviderListResponse {
  items: LlmProvider[]
  total: number
}

export interface LlmProviderInput {
  name: string
  provider_type: string
  base_url: string
  api_key?: string
  model: string
  timeout_ms?: number
  enabled?: boolean
  is_default?: boolean
}

export interface LlmProviderTestResult {
  ok: boolean
  message: string
  latency_ms: number | null
  model: string | null
}

// ── Credential（任务级凭据，P1-6） ──

export interface Credential {
  id: string
  name: string
  kind: 'env_var' | 'file'
  target: string
  secret_masked: string
  has_secret: boolean
  description: string | null
  created_at: string
  updated_at: string
}

export interface CredentialListResponse {
  items: Credential[]
  total: number
}

export interface CredentialInput {
  name: string
  kind: 'env_var' | 'file'
  target: string
  secret: string
  description?: string
}

export const api = {
  // Auth
  login: (data: { email: string; password: string }) =>
    request<TokenResponse>('/auth/login', { method: 'POST', body: JSON.stringify(data) }),

  register: (data: { email: string; password: string; display_name: string }) =>
    request<CurrentUser>('/auth/register', { method: 'POST', body: JSON.stringify(data) }),

  me: () => request<CurrentUser>('/auth/me'),

  // Tasks
  listTasks: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : ''
    return request<TaskListResponse>(`/tasks/${qs}`)
  },

  createTask: (data: {
    project_address: string
    vulnerability_description: string
    priority?: string
    project_ref?: string
    vulnerability_reasoning?: string
    credential_refs?: string[]
  }) => request<TaskDetail>('/tasks/', { method: 'POST', body: JSON.stringify(data) }),

  getTask: (id: string) => request<TaskDetail>(`/tasks/${id}`),

  cancelTask: (id: string) => request<TaskDetail>(`/tasks/${id}/cancel`, { method: 'POST' }),

  getTaskEvents: (id: string) => request<AgentEvent[]>(`/tasks/${id}/events`),

  // Reports
  getReportByTask: (taskId: string) => request<ReportDetail>(`/reports/task/${taskId}`),

  publishReport: (reportId: string) =>
    request<ReportDetail>(`/reports/${reportId}/publish`, { method: 'POST' }),

  // Evidence（P0-4）—— FormData 走单独 fetch，不经 request（不要 JSON Content-Type）
  uploadEvidence: (reportId: string, file: File, kind: 'artifact' | 'log' | 'screenshot' | 'poc' = 'artifact') => {
    const form = new FormData()
    form.append('file', file)
    form.append('kind', kind)
    const token = localStorage.getItem('crucible_token')
    return fetch(`${API_BASE}/reports/${reportId}/evidences`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      // 注意：不设 Content-Type，浏览器自动加 multipart boundary
      body: form,
    }).then(async (res): Promise<Evidence> => {
      if (res.status === 401) {
        handleUnauthorized()
        throw new Error('登录已过期，请重新登录')
      }
      if (res.status === 413) {
        throw new Error('文件超过 50MB 限制')
      }
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: '上传失败' }))
        throw new Error(e.detail || `HTTP ${res.status}`)
      }
      return res.json() as Promise<Evidence>
    })
  },

  listEvidences: (reportId: string) =>
    request<Evidence[]>(`/reports/${reportId}/evidences`),

  // Settings — LLM Providers
  listLlmProviders: () => request<LlmProviderListResponse>('/settings/llm/providers'),

  createLlmProvider: (data: LlmProviderInput) =>
    request<LlmProvider>('/settings/llm/providers', { method: 'POST', body: JSON.stringify(data) }),

  updateLlmProvider: (id: string, data: Partial<LlmProviderInput>) =>
    request<LlmProvider>(`/settings/llm/providers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),

  deleteLlmProvider: (id: string) =>
    request<void>(`/settings/llm/providers/${id}`, { method: 'DELETE' }),

  activateLlmProvider: (id: string) =>
    request<LlmProvider>(`/settings/llm/providers/${id}/activate`, { method: 'POST' }),

  testLlmProvider: (id: string) =>
    request<LlmProviderTestResult>(`/settings/llm/providers/${id}/test`, { method: 'POST' }),

  // Credentials（P1-6）
  listCredentials: () => request<CredentialListResponse>('/settings/credentials'),

  createCredential: (data: CredentialInput) =>
    request<Credential>('/settings/credentials', { method: 'POST', body: JSON.stringify(data) }),

  updateCredential: (id: string, data: Partial<Pick<CredentialInput, 'name' | 'secret' | 'description'>>) =>
    request<Credential>(`/settings/credentials/${id}`, { method: 'PUT', body: JSON.stringify(data) }),

  deleteCredential: (id: string) =>
    request<void>(`/settings/credentials/${id}`, { method: 'DELETE' }),
}
