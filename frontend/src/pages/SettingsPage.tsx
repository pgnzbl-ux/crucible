import { useEffect, useState } from 'react'
import {
  App,
  Button,
  Card,
  Collapse,
  Drawer,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import {
  ApiOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  KeyOutlined,
  PlusOutlined,
  ReloadOutlined,
  StarFilled,
  ThunderboltOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import {
  api,
  type Credential,
  type CredentialInput,
  type LlmProvider,
  type LlmProviderAgentTestResult,
  type LlmProviderInput,
} from '../shared/lib/api'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { RuntimePanel } from '../features/settings/RuntimePanel'

const { Text, Paragraph } = Typography

const DEFAULT_TEMPERATURE = 0.2
const DEFAULT_MAX_CONTEXT_TOKENS = 200_000
const DEFAULT_EFFORT = 'high'

const EFFORT_OPTIONS = [
  { value: 'low', label: 'low' },
  { value: 'medium', label: 'medium' },
  { value: 'high', label: 'high' },
  { value: 'xhigh', label: 'xhigh' },
  { value: 'max', label: 'max' },
  { value: 'auto', label: 'auto（模型默认）' },
]

const PROVIDER_TYPES: Record<string, {
  label: string
  defaultUrl: string
  defaultModel: string
  defaultAuthMode: 'api_key' | 'bearer'
}> = {
  deepseek: {
    label: 'DeepSeek 官方',
    defaultUrl: 'https://api.deepseek.com/anthropic',
    defaultModel: 'deepseek-v4-flash',
    defaultAuthMode: 'bearer',
  },
  anthropic: {
    label: 'Anthropic 官方',
    defaultUrl: 'https://api.anthropic.com',
    defaultModel: 'claude-sonnet-4',
    defaultAuthMode: 'api_key',
  },
  custom: {
    label: 'Anthropic 兼容（其他）',
    defaultUrl: 'https://',
    defaultModel: '',
    defaultAuthMode: 'bearer',
  },
}

/** 列表展示：openai_compat 与 custom 同为 Anthropic 兼容 */
function providerTypeLabel(type: string): string {
  if (type === 'openai_compat') return PROVIDER_TYPES.custom.label
  return PROVIDER_TYPES[type]?.label ?? type
}

function ProviderFormDrawer({
  open,
  onClose,
  editing,
}: {
  open: boolean
  onClose: () => void
  editing: LlmProvider | null
}) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const [testing, setTesting] = useState(false)
  const [agentTesting, setAgentTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [agentTestResult, setAgentTestResult] = useState<LlmProviderAgentTestResult | null>(null)

  const providerType = Form.useWatch('provider_type', form)

  useEffect(() => {
    if (!open) return
    if (editing) {
      form.setFieldsValue({
        name: editing.name,
        provider_type: editing.provider_type,
        auth_mode: editing.auth_mode,
        base_url: editing.base_url,
        model: editing.model,
        timeout_ms: editing.timeout_ms,
        temperature: editing.temperature,
        max_context_tokens: editing.max_context_tokens,
        effort: editing.effort,
        api_key: undefined,
      })
    } else {
      form.resetFields()
      form.setFieldsValue({
        provider_type: 'deepseek',
        auth_mode: 'bearer',
        timeout_ms: 600000,
        temperature: DEFAULT_TEMPERATURE,
        max_context_tokens: DEFAULT_MAX_CONTEXT_TOKENS,
        effort: DEFAULT_EFFORT,
      })
    }
    setTestResult(null)
    setAgentTestResult(null)
  }, [open, editing, form])

  const saveMutation = useMutation({
    mutationFn: (values: LlmProviderInput) =>
      editing
        ? api.updateLlmProvider(editing.id, values)
        : api.createLlmProvider(values),
    onSuccess: () => {
      message.success(editing ? 'Provider 已更新' : 'Provider 已创建')
      form.resetFields()
      setTestResult(null)
      setAgentTestResult(null)
      onClose()
      qc.invalidateQueries({ queryKey: ['llm-providers'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const handleTypeChange = (type: string) => {
    const preset = PROVIDER_TYPES[type]
    if (preset) {
      form.setFieldsValue({
        base_url: preset.defaultUrl,
        model: preset.defaultModel,
        auth_mode: preset.defaultAuthMode,
      })
    }
  }

  const handleTest = async () => {
    const values = await form.validateFields().catch(() => null)
    if (!values) return
    setTesting(true)
    setTestResult(null)
    try {
      // 编辑且未改 key → 复用已存 Provider 的凭据测试；否则用表单值走通用端点
      const result =
        editing && !values.api_key
          ? await api.testLlmProvider(editing.id)
          : await api.testLlmConnection({
              base_url: values.base_url,
              provider_type: values.provider_type,
              auth_mode: values.auth_mode,
              api_key: values.api_key,
              model: values.model,
              temperature: values.temperature,
              effort: values.effort,
            })
      setTestResult({ ok: result.ok, message: result.message })
    } catch (e) {
      setTestResult({ ok: false, message: (e as Error).message })
    } finally {
      setTesting(false)
    }
  }

  const handleAgentTest = async () => {
    if (!editing) return
    setAgentTesting(true)
    setAgentTestResult(null)
    try {
      setAgentTestResult(await api.testLlmProviderAgent(editing.id))
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setAgentTesting(false)
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={560}
      title={editing ? `编辑 Provider · ${editing.name}` : '新增 LLM Provider'}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => saveMutation.mutate(v)}
      >
        <Form.Item
          name="provider_type"
          label="接入预设"
          rules={[{ required: true }]}
          extra="须使用 Anthropic Messages 兼容端点（例如 DeepSeek 的 …/anthropic）。"
        >
          <Select
            options={Object.entries(PROVIDER_TYPES).map(([v, m]) => ({ value: v, label: m.label }))}
            onChange={handleTypeChange}
          />
        </Form.Item>
        <Form.Item
          name="auth_mode"
          label="认证方式"
          rules={[{ required: true }]}
          extra="Anthropic 官方一般用 X-Api-Key；DeepSeek 与多数网关用 Bearer。连接测试与 Agent 共用此设置。"
        >
          <Select
            options={[
              { value: 'api_key', label: 'X-Api-Key（Anthropic API Key）' },
              { value: 'bearer', label: 'Authorization: Bearer（网关 Token）' },
            ]}
          />
        </Form.Item>
        <Form.Item name="name" label="显示名称" rules={[{ required: true }]}>
          <Input placeholder="如 DeepSeek 官方" />
        </Form.Item>
        <Form.Item
          name="base_url"
          label="Base URL"
          rules={[{ required: true }]}
          extra="填写 Anthropic 兼容 API 根地址，不含 /v1/messages。"
        >
          <Input placeholder="https://api.deepseek.com/anthropic" />
        </Form.Item>
        <Form.Item
          name="model"
          label="模型"
          rules={[{ required: true }]}
          extra={providerType === 'deepseek' ? '常用：deepseek-v4-flash、deepseek-v4-pro。' : undefined}
        >
          <Input placeholder="deepseek-v4-flash" />
        </Form.Item>
        <Form.Item
          name="api_key"
          label={editing ? 'API Key（留空保持不变）' : 'API Key'}
          rules={editing ? [] : [{ required: true, message: '请输入 API Key' }]}
          extra="列表仅显示掩码，不会回显完整密钥。"
        >
          <Input.Password placeholder={editing ? 'sk-***（已配置）' : 'sk-...'} autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="timeout_ms"
          label="超时 (ms)"
          extra="Agent 与 Messages 请求共用；「测试连接」固定约 30 秒。"
        >
          <InputNumber min={10000} max={3600000} step={60000} style={{ width: '100%' }} />
        </Form.Item>

        <Collapse
          ghost
          style={{ marginBottom: 16 }}
          items={[
            {
              key: 'advanced',
              label: '高级设置',
              children: (
                <>
                  <Form.Item
                    name="temperature"
                    label="温度"
                    extra="作用于连接测试与 Messages 路径；Agent 沙箱当前不使用该值。"
                    rules={[{ required: true }]}
                  >
                    <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item
                    name="max_context_tokens"
                    label="最大上下文 (token)"
                    extra="供 Agent 按窗口管理上下文；自定义模型请按实际上限填写。"
                    rules={[{ required: true }]}
                  >
                    <InputNumber min={1024} max={2_000_000} step={1000} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item
                    name="effort"
                    label="思考强度"
                    extra="同时作用于 Agent 与 Messages；auto 表示沿用模型默认。"
                    rules={[{ required: true }]}
                  >
                    <Select options={EFFORT_OPTIONS} />
                  </Form.Item>
                </>
              ),
            },
          ]}
        />

        {testResult && (
          <div style={{ marginBottom: 16 }}>
            <Tag color={testResult.ok ? 'green' : 'red'}>
              {testResult.ok ? <CheckCircleOutlined /> : <ApiOutlined />} {testResult.message}
            </Tag>
          </div>
        )}

        {agentTestResult && (
          <Card size="small" style={{ marginBottom: 16 }}>
            <Space orientation="vertical" size={8}>
              <Tag color={agentTestResult.ok ? 'green' : 'red'}>
                {agentTestResult.ok ? <CheckCircleOutlined /> : <ApiOutlined />} {agentTestResult.message}
                {(agentTestResult.attempts ?? 1) > 1 ? (
                  <Text type="secondary" style={{ marginLeft: 6 }}>
                    （共 {agentTestResult.attempts} 次尝试）
                  </Text>
                ) : null}
              </Tag>
              <Space size={[4, 4]} wrap>
                {Object.entries({
                  read_tool: 'Read',
                  bash_tool: 'Bash',
                  mcp_submit: '结果提交',
                  multi_turn: '多轮',
                  // 平台项：校验的是本机凭据剥离逻辑，与 Provider 质量无关，恒绿不代表模型可用
                  credential_isolation: '凭据隔离·平台项',
                  single_terminal: '正常结束',
                }).map(([key, label]) => {
                  const passed = agentTestResult.checks[key as keyof typeof agentTestResult.checks]
                  return (
                    <Tag
                      key={key}
                      color={
                        key === 'credential_isolation'
                          ? passed ? 'blue' : 'red'
                          : passed ? 'green' : 'red'
                      }
                    >
                      {label}
                    </Tag>
                  )
                })}
              </Space>
              <Text type="secondary">
                {agentTestResult.model} · {agentTestResult.num_turns ?? '-'} 轮 · {agentTestResult.duration_ms ?? '-'} ms
              </Text>
              {!agentTestResult.ok && (agentTestResult.evidence?.length ?? 0) > 0 && (
                <div>
                  {(agentTestResult.evidence ?? []).map((line, i) => (
                    <Paragraph
                      key={i}
                      type="secondary"
                      style={{ marginBottom: 2, fontSize: 11, whiteSpace: 'pre-wrap' }}
                    >
                      {line}
                    </Paragraph>
                  ))}
                </div>
              )}
            </Space>
          </Card>
        )}

        <Space>
          <Button onClick={handleTest} loading={testing} icon={<ThunderboltOutlined />}>
            测试连接
          </Button>
          {editing && (
            <Button
              onClick={handleAgentTest}
              loading={agentTesting}
              icon={<ThunderboltOutlined />}
              title="使用已保存配置启动沙箱 Agent；修改表单后请先保存"
            >
              Agent 测试
            </Button>
          )}
          <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
            保存
          </Button>
        </Space>
      </Form>
    </Drawer>
  )
}

// ── LLM Provider 面板 ──

function ProviderPanel() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<LlmProvider | null>(null)

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['llm-providers'],
    queryFn: () => api.listLlmProviders(),
  })
  useErrorToast(isError, error, 'Provider 列表加载失败')

  const activateMutation = useMutation({
    mutationFn: (id: string) => api.activateLlmProvider(id),
    onSuccess: () => {
      message.success('已设为默认 Provider')
      qc.invalidateQueries({ queryKey: ['llm-providers'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteLlmProvider(id),
    onSuccess: () => {
      message.success('已删除')
      qc.invalidateQueries({ queryKey: ['llm-providers'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const columns: ColumnsType<LlmProvider> = [
    {
      title: 'Provider',
      dataIndex: 'name',
      render: (v: string, row) => (
        <div>
          <Space>{v}{row.is_default && <StarFilled style={{ color: 'var(--crucible-warning)' }} />}</Space>
          <div><Tag variant="filled">{providerTypeLabel(row.provider_type)}</Tag></div>
        </div>
      ),
    },
    {
      title: '模型 / 接口',
      dataIndex: 'model',
      ellipsis: true,
      render: (v: string, row) => (
        <div>
          <Text code>{v}</Text>
          <div><Text type="secondary" style={{ fontSize: 12 }}>{row.base_url}</Text></div>
        </div>
      ),
    },
    {
      title: '连接配置',
      dataIndex: 'has_api_key',
      width: 140,
      render: (v: boolean, row) => (
        <div>
          <Tag color={v ? 'green' : 'red'}>{v ? '凭据已配置' : '凭据未配置'}</Tag>
          <div><Text type="secondary" style={{ fontSize: 12 }}>{row.auth_mode === 'api_key' ? 'X-Api-Key' : 'Bearer'}</Text></div>
          <div><Text type="secondary" style={{ fontSize: 12 }}>超时 {Math.round(row.timeout_ms / 1000)} 秒</Text></div>
        </div>
      ),
    },
    {
      title: '使用状态',
      dataIndex: 'is_default',
      width: 90,
      render: (v: boolean) => (v ? <Tag color="gold">默认</Tag> : <Tag>备用</Tag>),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 160,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      render: (_, row) => (
        <Space size={4}>
          {!row.is_default && (
            <Button size="small" onClick={() => activateMutation.mutate(row.id)}>
              设为默认
            </Button>
          )}
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditing(row)
              setCreateOpen(true)
            }}
          />
          <Popconfirm
            title="删除该 Provider？"
            onConfirm={() => deleteMutation.mutate(row.id)}
            disabled={row.is_default}
          >
            <Button size="small" danger icon={<DeleteOutlined />} disabled={row.is_default} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Text type="secondary">
          管理 Anthropic 兼容的 LLM 接入。点「设为默认」后，Agent 任务将使用该 Provider。
        </Text>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); setCreateOpen(true) }}>
            新增 Provider
          </Button>
        </Space>
      </div>

      <Card className="crucible-card-hover">
        <Table
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          pagination={false}
          locale={{ emptyText: '暂无 Provider，点击右上角新增' }}
        />
      </Card>

      <ProviderFormDrawer
        open={createOpen}
        editing={editing}
        onClose={() => {
          setCreateOpen(false)
          setEditing(null)
        }}
      />
    </div>
  )
}

// ── 审计凭据管理 ──

function CredentialsPanel() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Credential | null>(null)
  const [form] = Form.useForm()
  const kind = Form.useWatch('kind', form)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['credentials'],
    queryFn: () => api.listCredentials(),
  })
  useErrorToast(isError, error, '凭据列表加载失败')

  const saveMutation = useMutation({
    mutationFn: (values: CredentialInput & { id?: string }) =>
      values.id
        ? api.updateCredential(values.id, values)
        : api.createCredential(values),
    onSuccess: () => {
      message.success(editing ? '凭据已更新' : '凭据已创建')
      form.resetFields()
      setOpen(false)
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['credentials'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteCredential(id),
    onSuccess: () => {
      message.success('已删除')
      qc.invalidateQueries({ queryKey: ['credentials'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const columns: ColumnsType<Credential> = [
    {
      title: '凭据 / 用途', dataIndex: 'name',
      render: (v: string, row) => (
        <div>
          <Space><KeyOutlined />{v}</Space>
          <div><Text type="secondary" style={{ fontSize: 12 }}>{row.description || '未填写用途说明'}</Text></div>
        </div>
      ),
    },
    {
      title: '注入位置', dataIndex: 'kind', width: 240,
      render: (v: string, row) => (
        <div>
          <Tag color={v === 'env_var' ? 'blue' : 'purple'}>{v === 'env_var' ? '环境变量' : '安全文件'}</Tag>
          <Text code style={{ fontSize: 12 }}>{row.target}</Text>
        </div>
      ),
    },
    {
      title: '配置状态', dataIndex: 'has_secret', width: 120,
      render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? '已配置' : '未配置'}</Tag>,
    },
    {
      title: '操作', key: 'actions', width: 110,
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" icon={<EditOutlined />} onClick={() => { setEditing(row); setOpen(true); form.setFieldsValue({ name: row.name, description: row.description }) }} />
          <Popconfirm title="删除该凭据？关联任务将失去此凭据。" onConfirm={() => deleteMutation.mutate(row.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
        运行分析时注入到环境：可作为环境变量，或写入密钥文件（任务结束后清除）。在「发起代码审计」时勾选关联。
      </Text>

      <Card
        className="crucible-card-hover"
        extra={
          <Button size="small" icon={<PlusOutlined />} onClick={() => { setEditing(null); form.resetFields(); setOpen(true) }}>
            新增凭据
          </Button>
        }
      >
        <Table
          rowKey="id"
          size="small"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          pagination={false}
          locale={{ emptyText: '暂无凭据，点击右上角新增' }}
        />
      </Card>

      <Drawer
        open={open}
        onClose={() => { setOpen(false); setEditing(null); form.resetFields() }}
        size={480}
        title={editing ? `编辑凭据 · ${editing.name}` : '新增任务凭据'}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={editing ? { kind: editing.kind } : { kind: 'env_var' }}
          onFinish={(v) => saveMutation.mutate({ ...v, id: editing?.id })}
        >
          <Form.Item name="name" label="显示名" rules={[{ required: true, message: '请输入显示名' }]}>
            <Input placeholder="如 目标站管理员账号" />
          </Form.Item>
          {!editing && (
            <>
              <Form.Item name="kind" label="注入方式" rules={[{ required: true }]}>
                <Select
                  options={[
                    { value: 'env_var', label: '环境变量' },
                    { value: 'file', label: '密钥文件' },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="target"
                label="目标"
                rules={[{ required: true, message: '请输入目标' }]}
                extra={kind === 'env_var' ? '环境变量名，如 DB_PASSWORD' : '文件名，如 tls.key（不可含路径）'}
              >
                <Input placeholder={kind === 'env_var' ? 'DB_PASSWORD' : 'tls.key'} />
              </Form.Item>
            </>
          )}
          <Form.Item
            name="secret"
            label={editing ? '凭据值（留空保持不变）' : '凭据值'}
            rules={editing ? [] : [{ required: true, message: '请输入凭据值' }]}
            extra="列表仅显示掩码，不会回显完整内容。"
          >
            <Input.Password placeholder={editing ? '***（已配置，留空不变）' : '输入凭据值'} autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="如 目标站 admin 账号密码，用于登录后验证 IDOR" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
            保存
          </Button>
        </Form>
      </Drawer>
    </div>
  )
}

export function SettingsPage() {
  let isAdmin = false
  try {
    const user = JSON.parse(localStorage.getItem('crucible_user') || '{}') as { is_admin?: boolean; role?: string }
    isAdmin = user.is_admin === true || user.role === 'admin'
  } catch {
    isAdmin = false
  }
  const items = [
    ...(isAdmin ? [{ key: 'providers', label: 'LLM Provider', children: <ProviderPanel /> }] : []),
    { key: 'credentials', label: '审计凭据', children: <CredentialsPanel /> },
    ...(isAdmin ? [{ key: 'runtime', label: '并发与资源', children: <RuntimePanel /> }] : []),
  ]

  return (
    <>
      <PageHeader
        title="设置"
        subtitle={isAdmin ? '管理 AI 模型、审计凭据与运行并发' : '管理当前账号的审计凭据'}
      />
      <PageContainer>
      <Tabs
        type="card"
        destroyOnHidden
        items={items}
      />
      </PageContainer>
    </>
  )
}
