import { useState } from 'react'
import {
  App,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  ApiOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  StarFilled,
  ThunderboltOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { api, type LlmProvider, type LlmProviderInput } from '../shared/lib/api'
import { AppLayout } from '../app/layout'

const { Title, Text } = Typography

const PROVIDER_TYPES: Record<string, { label: string; defaultUrl: string; defaultModel: string }> = {
  deepseek: {
    label: 'DeepSeek 官方',
    defaultUrl: 'https://api.deepseek.com/anthropic',
    defaultModel: 'deepseek-v4-flash',
  },
  tencent: {
    label: '腾讯云知识引擎',
    defaultUrl: 'https://api.lkeap.cloud.tencent.com/anthropic',
    defaultModel: 'deepseek-v3.2',
  },
  openai_compat: { label: 'OpenAI 兼容', defaultUrl: 'https://', defaultModel: '' },
  anthropic: { label: 'Anthropic 官方', defaultUrl: 'https://api.anthropic.com', defaultModel: 'claude-sonnet-4' },
  custom: { label: '自定义', defaultUrl: 'https://', defaultModel: '' },
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
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)

  const providerType = Form.useWatch('provider_type', form)

  const saveMutation = useMutation({
    mutationFn: (values: LlmProviderInput) =>
      editing
        ? api.updateLlmProvider(editing.id, values)
        : api.createLlmProvider(values),
    onSuccess: () => {
      message.success(editing ? 'Provider 已更新' : 'Provider 已创建')
      form.resetFields()
      setTestResult(null)
      onClose()
      qc.invalidateQueries({ queryKey: ['llm-providers'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const handleTypeChange = (type: string) => {
    const preset = PROVIDER_TYPES[type]
    if (preset && !form.getFieldValue('base_url')) {
      form.setFieldsValue({ base_url: preset.defaultUrl, model: preset.defaultModel })
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
          : await fetchTest({
              base_url: values.base_url,
              api_key: values.api_key,
              model: values.model,
            })
      setTestResult({ ok: result.ok, message: result.message })
    } catch (e) {
      setTestResult({ ok: false, message: (e as Error).message })
    } finally {
      setTesting(false)
    }
  }

  const fetchTest = async (target: { base_url: string; api_key?: string; model: string }) => {
    const res = await fetch('/api/v1/settings/llm/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(target),
    })
    return res.json()
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={560}
      title={editing ? `编辑 Provider · ${editing.name}` : '新增 LLM Provider'}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => saveMutation.mutate(v)}
        initialValues={
          editing
            ? {
                name: editing.name,
                provider_type: editing.provider_type,
                base_url: editing.base_url,
                model: editing.model,
                timeout_ms: editing.timeout_ms,
                enabled: editing.enabled,
              }
            : { provider_type: 'deepseek', timeout_ms: 600000, enabled: true }
        }
      >
        <Form.Item name="provider_type" label="服务商" rules={[{ required: true }]}>
          <Select
            options={Object.entries(PROVIDER_TYPES).map(([v, m]) => ({ value: v, label: m.label }))}
            onChange={handleTypeChange}
          />
        </Form.Item>
        <Form.Item name="name" label="显示名称" rules={[{ required: true }]}>
          <Input placeholder="如 DeepSeek 官方" />
        </Form.Item>
        <Form.Item name="base_url" label="Base URL (Anthropic 兼容端点)" rules={[{ required: true }]}>
          <Input placeholder="https://api.deepseek.com/anthropic" />
        </Form.Item>
        <Form.Item
          name="model"
          label="模型"
          rules={[{ required: true }]}
          extra={providerType === 'deepseek' ? 'deepseek-v4-flash（非思考）/ deepseek-v4-pro（思考）' : undefined}
        >
          <Input placeholder="deepseek-v4-flash" />
        </Form.Item>
        <Form.Item
          name="api_key"
          label={editing ? 'API Key（留空保持不变）' : 'API Key'}
          rules={editing ? [] : [{ required: true, message: '请输入 API Key' }]}
          extra="服务端加密存储，列表仅显示掩码"
        >
          <Input.Password placeholder={editing ? 'sk-***（已配置）' : 'sk-...'} autoComplete="new-password" />
        </Form.Item>
        <Form.Item name="timeout_ms" label="超时 (ms)">
          <InputNumber min={10000} max={3600000} step={60000} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="enabled" label="启用" valuePropName="checked">
          <Switch />
        </Form.Item>

        {testResult && (
          <div style={{ marginBottom: 16 }}>
            <Tag color={testResult.ok ? 'green' : 'red'}>
              {testResult.ok ? <CheckCircleOutlined /> : <ApiOutlined />} {testResult.message}
            </Tag>
          </div>
        )}

        <Space>
          <Button onClick={handleTest} loading={testing} icon={<ThunderboltOutlined />}>
            测试连接
          </Button>
          <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
            保存
          </Button>
        </Space>
      </Form>
    </Drawer>
  )
}

export function SettingsPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<LlmProvider | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['llm-providers'],
    queryFn: () => api.listLlmProviders(),
    refetchInterval: 8000,
  })

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
      title: '名称',
      dataIndex: 'name',
      render: (v: string, row) => (
        <Space>
          {v}
          {row.is_default && <StarFilled style={{ color: '#faad14' }} />}
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'provider_type',
      width: 140,
      render: (v: string) => <Tag>{PROVIDER_TYPES[v]?.label ?? v}</Tag>,
    },
    {
      title: '模型',
      dataIndex: 'model',
      width: 160,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      ellipsis: true,
      render: (v: string) => <Text code style={{ fontSize: 12 }}>{v}</Text>,
    },
    {
      title: 'API Key',
      dataIndex: 'api_key_masked',
      width: 130,
      render: (v: string, row) =>
        row.has_api_key ? <Text code>{v}</Text> : <Text type="secondary">未配置</Text>,
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 90,
      render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
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
          <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(row)} />
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
    <AppLayout>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Title level={4} style={{ marginBottom: 4 }}>
            设置 · LLM Provider
          </Title>
          <Text type="secondary">
            管理 AI 模型接入（DeepSeek / 腾讯云等 Anthropic 兼容端点），新任务将使用默认 Provider
          </Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); setCreateOpen(true) }}>
            新增 Provider
          </Button>
        </Space>
      </div>

      <Card>
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
        onClose={() => { setCreateOpen(false); setEditing(null) }}
      />
    </AppLayout>
  )
}
