const API_BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export function isUnauthorizedError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}

export function handleUnauthorized() {
  localStorage.removeItem('crucible_token')
  localStorage.removeItem('crucible_user')
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
}

export function parseApiError(body: unknown, status: number): ApiError {
  const fallback = `HTTP ${status}`
  if (!body || typeof body !== 'object') return new ApiError(fallback, status)
  const rec = body as Record<string, unknown>
  let message = fallback
  let code: string | undefined
  const envelope = rec.error
  if (envelope && typeof envelope === 'object') {
    const env = envelope as { message?: unknown; code?: unknown }
    if (typeof env.message === 'string' && env.message) message = env.message
    if (typeof env.code === 'string' && env.code) code = env.code
  }
  const detail = rec.detail
  if (message === fallback && detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const nested = detail as { message?: unknown; code?: unknown }
    if (typeof nested.message === 'string' && nested.message) message = nested.message
    if (!code && typeof nested.code === 'string' && nested.code) code = nested.code
  }
  if (message === fallback && typeof detail === 'string' && detail) message = detail
  if (message === fallback && Array.isArray(detail) && detail[0] && typeof detail[0] === 'object') {
    const msg = (detail[0] as { msg?: unknown }).msg
    if (typeof msg === 'string' && msg) message = msg
  }
  return new ApiError(message, status, code)
}

export async function rejectIfNotOk(res: Response, hadAuth = false): Promise<void> {
  if (res.ok) return
  if (res.status === 401 && hadAuth) {
    handleUnauthorized()
    throw new ApiError('登录已过期，请重新登录', 401, 'UNAUTHORIZED')
  }
  const body = await res.json().catch(() => ({ detail: '请求失败' }))
  throw parseApiError(body, res.status)
}

async function request<T>(
  path: string,
  options?: RequestInit & { allow404?: boolean; skipAuth?: boolean },
): Promise<T> {
  const { allow404, skipAuth, ...fetchOpts } = options ?? {}
  const token = localStorage.getItem('crucible_token')
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(fetchOpts.headers as Record<string, string>),
  }
  const sendAuth = Boolean(token && !skipAuth)
  if (sendAuth && token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(`${API_BASE}${path}`, { ...fetchOpts, headers })
  if (res.status === 404 && allow404) {
    return null as T
  }
  await rejectIfNotOk(res, sendAuth)
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
  project_id: string | null
  project_ref: string | null
  project_ref_type: 'branch' | 'tag' | 'commit' | null
  status: string
  verdict: string | null
  priority: string
  source_type: string
  task_type?: 'verify' | 'discovery'
  source_alert_group_id?: string | null
  finding_count: number
  pending_review_count: number
  confirmed_count: number
  report_status: string | null
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
  project_ref_type: 'branch' | 'tag' | 'commit' | null
  clone_depth: number | null
  vulnerability_description: string
  vulnerability_reasoning: string | null
  credential_refs: string[]
  runs: RunSummary[]
}

// 节点状态(6 节点步骤条数据源)
export interface NodeUsage {
  prompt_tokens: number
  completion_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
  total_tokens: number
}

export interface NodeRun {
  id: string
  node_index: number
  node_key: string  // 拓扑节点(discovery-spec §4.2.4)
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled'
  attempt: number
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  output?: Record<string, unknown>
  /** 本 run 该节点台账聚合；无消耗时省略 */
  usage?: NodeUsage
}

// 6 档判定(对齐后端 verdict)
export type Verdict = 'confirmed' | 'partial' | 'code_reachable' | 'code_smell' | 'false_positive' | 'not_reproduced' | 'needs_review'

export interface TaskListResponse {
  items: TaskSummary[]
  total: number
  limit: number
  offset: number
}

export interface TaskStats {
  total: number
  by_status: Record<string, number>
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
  owner_id: string
  status: string
  conclusion: string
  title: string
  summary: string | null
  reasoning: string | null
  evidence_summary: string | null
  artifact_key: string | null
  // 结构化字段(阶段 1 新增)
  verdict: string | null
  cvss_score: number | null
  severity: string | null
  vulnerable_file: string | null
  poc_language: string | null
  poc_filename: string | null
  poc_code: string | null
  poc_usage: string | null
  report_data: Record<string, unknown> | null
  md_artifact_key: string | null
  docx_artifact_key: string | null
  published_at: string | null
  created_at: string
  updated_at: string
  evidence: Evidence[]
}

export interface ReportSummary {
  id: string
  task_id: string
  project_address: string | null
  project_ref: string | null
  task_type: 'verify' | 'discovery' | null
  document_kind: string | null
  status: string
  conclusion: string
  title: string
  summary: string | null
  verdict: string | null
  severity: string | null
  published_at: string | null
  created_at: string
  updated_at: string
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

// Project(阶段 1 新增)
export interface Project {
  id: string
  name: string
  git_url: string
  source_type?: 'git' | 'local_upload' | string
  default_ref: string | null
  default_ref_type?: 'branch' | 'tag' | 'commit' | null
  description: string | null
  owner_id: string
  detected_language: string | null
  detected_framework: string | null
  is_web: boolean | null
  last_cloned_at: string | null
  created_at: string
  updated_at: string
  source_refs?: { ref_type: string; ref_name: string }[]
}

export interface SourceArtifact {
  id: string
  git_url: string
  git_host: string
  project_key: string
  repo_dirname: string
  ref_type: string
  ref_name: string
  commit_sha: string
  object_url: string
  size_bytes: number | null
  created_at: string
  updated_at: string
}

export interface LabContainer {
  name: string
  status: string
  ports: string
  image: string
}

export interface Lab {
  id: string
  project_id: string
  commit_sha: string
  status: string
  target_url: string | null
  ttl_remaining_seconds: number | null
  containers: LabContainer[]
  live_task_count: number
  error_message?: string | null
}

export interface LabGroup {
  project_id: string
  project_name: string
  labs: Lab[]
}

export type LabAction = 'stop' | 'start' | 'rebuild'
export type LabContainerAction = 'stop' | 'start' | 'restart'

export interface LlmProvider {
  id: string
  name: string
  provider_type: string
  auth_mode: 'api_key' | 'bearer'
  base_url: string
  api_key_masked: string
  has_api_key: boolean
  model: string
  timeout_ms: number
  temperature: number
  max_context_tokens: number
  effort: string
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
  auth_mode?: 'api_key' | 'bearer'
  base_url: string
  api_key?: string
  model: string
  timeout_ms?: number
  temperature?: number
  max_context_tokens?: number
  effort?: string
  is_default?: boolean
}

export interface LlmProviderTestResult {
  ok: boolean
  message: string
  latency_ms: number | null
  model: string | null
}

export interface LlmAgentCanaryChecks {
  read_tool: boolean
  bash_tool: boolean
  mcp_submit: boolean
  multi_turn: boolean
  credential_isolation: boolean
  single_terminal: boolean
}

export interface LlmProviderAgentTestResult {
  ok: boolean
  message: string
  checks: LlmAgentCanaryChecks
  provider_id: string
  model: string
  duration_ms: number | null
  num_turns: number | null
  usage: Record<string, number>
}

// ── Credential（任务级凭据） ──

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

export interface RuntimeSettings {
  max_concurrent_tasks: number
  max_concurrent_agent_runners: number
  lead_verify_per_task: number
  reproduce_per_lab: number
  max_allowed: number
  agent_runner_max_allowed: number
  lead_verify_max_allowed: number
  reproduce_max_allowed: number
  worker_pool: 'prefork'
}

export type RuntimeSettingsInput = Pick<
  RuntimeSettings,
  | 'max_concurrent_tasks'
  | 'max_concurrent_agent_runners'
  | 'lead_verify_per_task'
  | 'reproduce_per_lab'
>

export const api = {
  // Auth
  login: (data: { email: string; password: string }) =>
    request<TokenResponse>('/auth/login', { method: 'POST', body: JSON.stringify(data), skipAuth: true }),

  register: (data: { email: string; password: string; display_name: string }) =>
    request<CurrentUser>('/auth/register', { method: 'POST', body: JSON.stringify(data), skipAuth: true }),

  me: () => request<CurrentUser>('/auth/me'),

  authSetup: () => request<{ needs_setup: boolean }>('/auth/setup', { skipAuth: true }),

  // Tasks
  listTasks: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : ''
    return request<TaskListResponse>(`/tasks/${qs}`)
  },

  getTaskStats: () => request<TaskStats>('/tasks/stats'),

  createTask: (data: {
    project_address: string
    task_type?: 'verify' | 'discovery'
    vulnerability_description?: string
    priority?: string
    project_ref?: string
    project_ref_type?: 'branch' | 'tag' | 'commit'
    clone_depth?: number
    source_type?: 'git' | 'local_upload'
    vulnerability_reasoning?: string
    credential_refs?: string[]
  }) => request<TaskDetail>('/tasks/', { method: 'POST', body: JSON.stringify(data) }),

  createTaskFromUpload: (data: {
    file: File
    task_type: 'verify' | 'discovery'
    vulnerability_description?: string
    name?: string
    priority?: string
    vulnerability_reasoning?: string
    credential_refs?: string[]
  }) => {
    const form = new FormData()
    form.append('file', data.file)
    form.append('task_type', data.task_type)
    if (data.vulnerability_description) form.append('vulnerability_description', data.vulnerability_description)
    if (data.name) form.append('name', data.name)
    if (data.priority) form.append('priority', data.priority)
    if (data.vulnerability_reasoning) form.append('vulnerability_reasoning', data.vulnerability_reasoning)
    if (data.credential_refs?.length) {
      form.append('credential_refs', JSON.stringify(data.credential_refs))
    }
    const token = localStorage.getItem('crucible_token')
    return fetch(`${API_BASE}/tasks/upload`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    }).then(async (res): Promise<TaskDetail> => {
      if (res.status === 413) {
        throw new ApiError('源码包超过 200MB 限制', 413)
      }
      await rejectIfNotOk(res, Boolean(token))
      return res.json() as Promise<TaskDetail>
    })
  },

  getTask: (id: string) => request<TaskDetail>(`/tasks/${id}`),

  cancelTask: (id: string) => request<TaskDetail>(`/tasks/${id}/cancel`, { method: 'POST' }),

  getTaskEvents: (id: string) => request<AgentEvent[]>(`/tasks/${id}/events?limit=1000`),
  issueSseTicket: (id: string) =>
    request<{ ticket: string; expires_in: number }>(`/tasks/${id}/events/ticket`, {
      method: 'POST',
    }),

  // Reports
  listReports: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : ''
    return request<{ items: ReportSummary[]; total: number; limit: number; offset: number }>(`/reports/${qs}`)
  },

  getReport: (reportId: string) => request<ReportDetail>(`/reports/${reportId}`),

  getReportByTask: (taskId: string) =>
    request<ReportDetail | null>(`/reports/task/${taskId}`, { allow404: true }),

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
      if (res.status === 413) {
        throw new ApiError('文件超过 50MB 限制', 413)
      }
      await rejectIfNotOk(res, Boolean(token))
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

  testLlmProviderAgent: (id: string) =>
    request<LlmProviderAgentTestResult>(`/settings/llm/providers/${id}/agent-test`, { method: 'POST' }),

  testLlmConnection: (data: {
    base_url: string
    provider_type?: string
    auth_mode?: 'api_key' | 'bearer'
    api_key?: string
    model: string
    temperature?: number
    effort?: string
  }) =>
    request<LlmProviderTestResult>('/settings/llm/test', { method: 'POST', body: JSON.stringify(data) }),

  // Credentials
  listCredentials: () => request<CredentialListResponse>('/settings/credentials'),

  createCredential: (data: CredentialInput) =>
    request<Credential>('/settings/credentials', { method: 'POST', body: JSON.stringify(data) }),

  updateCredential: (id: string, data: Partial<Pick<CredentialInput, 'name' | 'secret' | 'description'>>) =>
    request<Credential>(`/settings/credentials/${id}`, { method: 'PUT', body: JSON.stringify(data) }),

  deleteCredential: (id: string) =>
    request<void>(`/settings/credentials/${id}`, { method: 'DELETE' }),

  getRuntimeSettings: () => request<RuntimeSettings>('/settings/runtime'),

  updateRuntimeSettings: (data: RuntimeSettingsInput) =>
    request<RuntimeSettings>('/settings/runtime', { method: 'PUT', body: JSON.stringify(data) }),

  // Tasks — 阶段 1 新增(retry / delete / nodes)
  retryTask: (id: string, fromNode?: string) =>
    request<{ task_id: string; run_id: string; status: string; from_node: string | null }>(
      `/tasks/${id}/retry${fromNode ? `?from_node=${encodeURIComponent(fromNode)}` : ''}`,
      { method: 'POST' },
    ),

  deleteTask: (id: string, hard = false) =>
    request<void>(`/tasks/${id}${hard ? '?hard=true' : ''}`, {
      method: 'DELETE',
      headers: hard ? { 'X-Confirm': 'true' } : undefined,
    }),

  getRunNodes: (taskId: string, runId: string) =>
    request<NodeRun[]>(`/tasks/${taskId}/runs/${runId}/nodes`),

  // Reports — 导出（带鉴权，避免 window.open 丢 token）
  exportReportUrl: (reportId: string, format: 'json' | 'md' = 'json') =>
    `${API_BASE}/reports/${reportId}/export?format=${format}`,

  // Projects — 阶段 1 新增
  listProjects: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : ''
    return request<{ items: Project[]; total: number }>(`/projects/${qs}`)
  },
  createProject: (data: {
    name: string
    git_url: string
    default_ref?: string
    default_ref_type?: 'branch' | 'tag' | 'commit'
    description?: string
  }) =>
    request<Project>('/projects/', { method: 'POST', body: JSON.stringify(data) }),
  uploadProject: (data: { file: File; name: string; description?: string }) => {
    const form = new FormData()
    form.append('file', data.file)
    form.append('name', data.name)
    if (data.description) form.append('description', data.description)
    const token = localStorage.getItem('crucible_token')
    return fetch(`${API_BASE}/projects/upload`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    }).then(async (res): Promise<Project> => {
      if (res.status === 413) {
        throw new ApiError('源码包超过 200MB 限制', 413)
      }
      await rejectIfNotOk(res, Boolean(token))
      return res.json() as Promise<Project>
    })
  },
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  updateProject: (
    id: string,
    data: Partial<{
      name: string
      default_ref: string
      default_ref_type: 'branch' | 'tag' | 'commit'
      description: string
    }>,
  ) =>
    request<Project>(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: 'DELETE' }),
  listProjectArtifacts: (id: string) =>
    request<{ items: SourceArtifact[]; total: number }>(`/projects/${id}/artifacts`),
  deleteProjectArtifact: (projectId: string, artifactId: string) =>
    request<void>(`/projects/${projectId}/artifacts/${artifactId}`, { method: 'DELETE' }),

  // Findings(复核台, discovery-spec §9.1)
  getFindingStats: () => request<FindingStats>('/findings/stats'),
  listAlertGroups: (params?: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params ?? {})) {
      if (v !== undefined && v !== '' && v !== null) qs.set(k, String(v))
    }
    const suffix = qs.toString() ? `?${qs}` : ''
    return request<AlertGroupListResponse>(`/findings/groups${suffix}`)
  },
  listAlertGroupIds: (params?: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params ?? {})) {
      if (v !== undefined && v !== '' && v !== null) qs.set(k, String(v))
    }
    const suffix = qs.toString() ? `?${qs}` : ''
    return request<{ total: number; ids: string[] }>(`/findings/groups/ids${suffix}`)
  },
  getAlertGroup: (id: string) => request<AlertGroupDetail>(`/findings/groups/${id}`),
  deleteAlertGroup: (id: string) =>
    request<void>(`/findings/groups/${id}`, { method: 'DELETE' }),
  batchDeleteAlertGroups: (ids: string[]) =>
    request<{ deleted: string[]; skipped: { id: string; reason: string }[] }>(
      '/findings/groups/batch-delete',
      { method: 'POST', body: JSON.stringify({ ids }) },
    ),
  reviewAlertGroup: (
    id: string,
    data: {
      action: 'confirm' | 'reject' | 'revise_cwe' | 'adjust_confidence'
      reason_tags?: string[]
      reason_text?: string | null
      cwe?: string | null
      confidence?: number | null
    },
  ) => request<AlertGroupSummary>(`/findings/groups/${id}/review`, { method: 'POST', body: JSON.stringify(data) }),
  reviveAlertGroup: (id: string) =>
    request<{ id: string; status: string }>(`/findings/groups/${id}/revive`, { method: 'POST' }),
  dispatchAlertGroup: (id: string, include_engine_conclusion = false) =>
    request<{ group_id: string; verification_task_id: string }>(
      `/findings/groups/${id}/dispatch`,
      { method: 'POST', body: JSON.stringify({ include_engine_conclusion }) },
    ),

  // Labs
  listLabs: () => request<{ items: LabGroup[] }>('/labs'),
  getLab: (id: string) => request<Lab>(`/labs/${id}`),
  labAction: (id: string, action: LabAction) =>
    request<{ status: string }>(`/labs/${id}/actions/${action}`, { method: 'POST' }),
  deleteLab: (id: string) => request<{ status: string }>(`/labs/${id}`, { method: 'DELETE' }),
  labContainerAction: (id: string, name: string, action: LabContainerAction) =>
    request<{ status: string }>(
      `/labs/${id}/containers/${encodeURIComponent(name)}/actions/${action}`,
      { method: 'POST' },
    ),
  deleteLabContainer: (id: string, name: string) =>
    request<{ status: string }>(`/labs/${id}/containers/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),
}

// ── 发现侧·复核台(discovery-spec §9) ──

export interface FindingEvidenceRaw {
  confidence?: string
  category?: string | null
  has_dataflow?: boolean
  rule_class?: string
  entropy?: number
  called?: boolean | null
  unimportant?: boolean
  [key: string]: unknown
}

export interface FindingSummary {
  id: string
  engine: string
  rule_id: string
  cwe: string | null
  severity: string | null
  file_path: string
  line_start: number | null
  line_end: number | null
  message: string
  source_to_sink?: unknown[] | null
  code_snippet?: string | null
  /** 降噪/二审证据元数据（已脱敏）；非引擎结论措辞 */
  raw?: FindingEvidenceRaw | null
}

export interface AlertGroupSummary {
  id: string
  task_id: string
  project_id: string | null
  project_address: string | null
  project_ref: string | null
  audit_created_at: string | null
  cwe: string | null
  cwe_source: string
  vulnerability_title: string
  representative_rule_id: string | null
  representative_message: string | null
  severity: string | null
  primary_engine: string | null
  screening_status: string
  screening_summary: string
  screening_reasons: string[]
  file_path: string
  function_symbol: string | null
  line_span: string | null
  member_count: number
  engine_set: string[]
  status: string
  clue_grade: string | null
  ai_verdict: string | null
  ai_confidence: number | null
  priority: string | null
  resolution: string | null
  created_at: string | null
  updated_at: string | null
}

export interface AlertGroupListResponse {
  total: number
  items: AlertGroupSummary[]
}

export interface FindingStats {
  total: number
  by_status: Record<string, number>
  by_resolution: Record<string, number>
  by_queue: Record<string, number>
}

export interface AdjudicationDetail {
  id: string
  attempt: number
  verdict: string
  confidence: number | null
  why: string[]
  evidence: { file?: string; lines?: string }[]
  need: string[]
  prompt_text: string
  response_text: string
  usage: Record<string, number>
  created_at: string | null
}

export interface ReviewActionDetail {
  id: string
  action: string
  reason_tags: string[]
  reason_text: string | null
  user_id: string
  created_at: string | null
}

export interface LeadRunSummary {
  id: string
  status: string
  verdict: string | null
  gate_verdict: string | null
  error: string | null
  created_at: string | null
  updated_at: string | null
}

export interface AlertGroupDetail extends AlertGroupSummary {
  members: FindingSummary[]
  representative: FindingSummary | null
  adjudications: AdjudicationDetail[]
  reviews: ReviewActionDetail[]
  lead_runs: LeadRunSummary[]
  verification_task_id: string | null
  verification_verdict: string | null
}
