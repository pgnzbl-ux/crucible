const API_BASE = '/api/v1'

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
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '请求失败' }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ── 类型 ──

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
  evidence: unknown[]
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

export const api = {
  // Auth
  login: (data: { email: string; password: string }) =>
    request('/auth/login', { method: 'POST', body: JSON.stringify(data) }),

  register: (data: { email: string; password: string; display_name: string }) =>
    request('/auth/register', { method: 'POST', body: JSON.stringify(data) }),

  me: () => request('/auth/me'),

  // Tasks
  listTasks: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : ''
    return request<TaskListResponse>(`/tasks${qs}`)
  },

  createTask: (data: {
    project_address: string
    vulnerability_description: string
    priority?: string
    project_ref?: string
    vulnerability_reasoning?: string
  }) => request<TaskDetail>('/tasks/', { method: 'POST', body: JSON.stringify(data) }),

  getTask: (id: string) => request<TaskDetail>(`/tasks/${id}`),

  cancelTask: (id: string) => request<TaskDetail>(`/tasks/${id}/cancel`, { method: 'POST' }),

  getTaskEvents: (id: string) => request<AgentEvent[]>(`/tasks/${id}/events`),

  // Reports
  getReportByTask: (taskId: string) => request<ReportDetail>(`/reports/task/${taskId}`),

  publishReport: (reportId: string) =>
    request<ReportDetail>(`/reports/${reportId}/publish`, { method: 'POST' }),

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
}
