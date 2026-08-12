import { useEffect, useState } from 'react'
import {
  Alert,
  App,
  Badge,
  Button,
  Descriptions,
  Drawer,
  Form,
  Input,
  List,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Timeline,
  Typography,
  Empty,
  Divider,
  Upload,
  Card,
  Skeleton,
} from 'antd'
import type { UploadProps } from 'antd'
import {
  BugOutlined,
  DeleteOutlined,
  DownloadOutlined,
  PaperClipOutlined,
  PauseCircleOutlined,
  PlusOutlined,
  RedoOutlined,
  ReloadOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { api, type TaskDetail, type TaskSummary, type AgentEvent, type ReportDetail, type Evidence } from '../shared/lib/api'
import { getStatusMeta, getPriorityMeta, getConclusionMeta, getVerdictMeta, EVENT_PHASE_LABELS } from '../shared/lib/meta'
import { useTaskEvents, type SSEEvent } from '../shared/hooks/useTaskEvents'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'

const { Title, Paragraph, Text } = Typography

// ── 任务状态流 → Timeline 展示 ──

function eventMessage(ev: AgentEvent): string {
  const p = ev.payload
  const msg = p.message as string | undefined
  if (msg) return msg
  if (ev.event_type === 'tool.call.completed') {
    return `调用工具: ${String(p.tool ?? 'unknown')}`
  }
  if (ev.event_type === 'agent.completed') {
    return `Agent 完成，结论: ${String(p.conclusion ?? '')}`
  }
  return ev.event_type
}

// ── 详情抽屉 ──

function TaskDetailDrawer({
  taskId,
  open,
  onClose,
}: {
  taskId: string | null
  open: boolean
  onClose: () => void
}) {
  const { message } = App.useApp()
  const qc = useQueryClient()

  const { data: task, isLoading: taskLoading } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId!),
    enabled: !!taskId,
  })

  // SSE 实时事件流（drawer 打开 + task 处于活跃状态时启用）
  const running = task ? ['queued', 'running'].includes(task.status) : true
  const sseEnabled = open && !!taskId && running
  const { events: sseEvents, status: sseStatus, error: sseError } = useTaskEvents(taskId, { enabled: sseEnabled })

  // 收到 agent.completed / agent.failed → 立即刷新 task 与 report（不再 3s 轮询）
  useEffect(() => {
    const last = sseEvents[sseEvents.length - 1]
    if (!last) return
    if (last.type === 'agent.completed' || last.type === 'agent.failed') {
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['task-report', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
    }
  }, [sseEvents, qc, taskId])

  // 把 SSE 累积事件投影成 AgentEvent[]（与原 Timeline 数据形状兼容）
  const events: AgentEvent[] | undefined = sseEvents.length
    ? sseEvents.map((ev) => ({
        id: `${ev.sequence ?? 'x'}`,
        run_id: ev.run_id ?? '',
        sequence: ev.sequence ?? 0,
        event_type: ev.type,
        payload: (ev.event ?? {}) as Record<string, unknown>,
        source: 'sse',
        created_at: new Date().toISOString(),
      }))
    : undefined

  const { data: report } = useQuery({
    queryKey: ['task-report', taskId],
    queryFn: () => api.getReportByTask(taskId!),
    enabled: !!taskId,
    retry: false,
  })

  const publishMutation = useMutation({
    mutationFn: (rid: string) => api.publishReport(rid),
    onSuccess: () => message.success('报告已发布'),
    onError: (e: Error) => message.error(e.message),
  })

  if (taskLoading && !task) {
    return (
      <Drawer open={open} onClose={onClose} width={720} title="任务详情">
        <Skeleton active paragraph={{ rows: 8 }} />
      </Drawer>
    )
  }

  if (!task) return <Drawer open={open} onClose={onClose} width={720} title="任务详情" />

  const st = getStatusMeta(task.status)
  const timelineItems =
    events?.map((ev) => {
      const p = ev.payload as Record<string, unknown>
      const phase = p.phase as string | undefined
      return {
        color: ev.event_type.includes('failed') ? 'red' : running ? 'blue' : 'green',
        children: (
          <div>
            <Text strong>{EVENT_PHASE_LABELS[phase ?? ''] ?? eventMessage(ev)}</Text>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {eventMessage(ev)}
              </Text>
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {dayjs(ev.created_at).format('HH:mm:ss')}
            </Text>
          </div>
        ),
      }
    }) ?? []

  return (
    <Drawer open={open} onClose={onClose} width={720} title={`任务详情 · ${task.id.slice(0, 8)}`}>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        {/* 基础信息 */}
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="项目地址" span={2}>
            <Text code>{task.project_address}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="引用">{task.project_ref ?? '默认分支'}</Descriptions.Item>
          <Descriptions.Item label="优先级">
            <Tag color={getPriorityMeta(task.priority).color}>{getPriorityMeta(task.priority).label}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={st.color}>{st.label}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {dayjs(task.created_at).format('YYYY-MM-DD HH:mm:ss')}
          </Descriptions.Item>
          <Descriptions.Item label="漏洞描述" span={2}>
            <Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
              {task.vulnerability_description}
            </Paragraph>
          </Descriptions.Item>
        </Descriptions>

        {task.status === 'failed' && task.runs[0]?.error_message && (
          <Alert type="error" showIcon message="执行失败" description={task.runs[0].error_message} />
        )}

        {/* Agent 执行进度 */}
        <div>
          <Space style={{ marginBottom: 12, width: '100%', justifyContent: 'space-between' }}>
            <Title level={5} style={{ margin: 0 }}>
              Agent 执行进度
            </Title>
            {sseEnabled && (
              <Badge
                status={
                  sseStatus === 'open'
                    ? 'success'
                    : sseStatus === 'reconnecting'
                      ? 'warning'
                      : sseStatus === 'connecting'
                        ? 'processing'
                        : 'default'
                }
                text={
                  sseStatus === 'open'
                    ? '实时'
                    : sseStatus === 'reconnecting'
                      ? '重连中...'
                      : sseStatus === 'connecting'
                        ? '连接中'
                        : sseStatus === 'closed'
                          ? '已断开'
                          : '离线'
                }
              />
            )}
          </Space>
          {sseError && sseStatus === 'reconnecting' && (
            <Alert type="warning" showIcon message={sseError} style={{ marginBottom: 12 }} />
          )}
          {events && events.length > 0 ? (
            <Timeline items={timelineItems} />
          ) : (
            <Empty description="暂无执行事件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </div>

        {/* 分析推理 */}
        {task.vulnerability_reasoning && (
          <>
            <Divider style={{ margin: '8px 0' }} />
            <div>
              <Title level={5}>分析推理</Title>
              <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{task.vulnerability_reasoning}</Paragraph>
            </div>
          </>
        )}

        {/* 验证报告 */}
        {report && (
          <>
            <Divider style={{ margin: '8px 0' }} />
            <div>
              <Title level={5}>验证报告</Title>
              <Descriptions column={2} size="small" bordered>
                <Descriptions.Item label="状态">{report.status}</Descriptions.Item>
                <Descriptions.Item label="结论">
                  <Tag color={getConclusionMeta(report.conclusion).color}>
                    {getConclusionMeta(report.conclusion).label}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="标题" span={2}>
                  {report.title}
                </Descriptions.Item>
                <Descriptions.Item label="摘要" span={2}>
                  {report.summary}
                </Descriptions.Item>
                <Descriptions.Item label="产物归档" span={2}>
                  <Text code style={{ fontSize: 12 }}>{report.artifact_key ?? '-'}</Text>
                </Descriptions.Item>
              </Descriptions>
              {report.status !== 'published' && (
                <Button
                  style={{ marginTop: 12 }}
                  type="primary"
                  onClick={() => publishMutation.mutate(report.id)}
                  loading={publishMutation.isPending}
                >
                  发布报告
                </Button>
              )}
              <EvidenceList reportId={report.id} />
            </div>
          </>
        )}
      </Space>
    </Drawer>
  )
}

// ── 证据列表 + 上传（P0-4） ──

function EvidenceList({ reportId }: { reportId: string }) {
  const { message } = App.useApp()
  const qc = useQueryClient()

  const { data: evidences, isLoading } = useQuery({
    queryKey: ['report-evidences', reportId],
    queryFn: () => api.listEvidences(reportId),
  })

  const uploadProps: UploadProps = {
    multiple: false,
    showUploadList: false,
    accept: undefined, // 不限制类型（证据可能是 log/png/poc 任意格式）
    customRequest: async (options) => {
      const { file, onSuccess, onError } = options
      try {
        const ev = await api.uploadEvidence(reportId, file as File)
        message.success(`已上传: ${ev.file_name}`)
        qc.invalidateQueries({ queryKey: ['report-evidences', reportId] })
        qc.invalidateQueries({ queryKey: ['task-report'] })
        onSuccess?.(ev)
      } catch (e) {
        message.error((e as Error).message)
        onError?.(e as Error)
      }
    },
  }

  return (
    <div style={{ marginTop: 12 }}>
      <Space style={{ marginBottom: 8, width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary" style={{ fontSize: 13 }}>
          <PaperClipOutlined /> 证据文件（{evidences?.length ?? 0}）
        </Text>
        <Upload {...uploadProps}>
          <Button size="small" icon={<UploadOutlined />}>上传证据</Button>
        </Upload>
      </Space>
      <List<Evidence>
        size="small"
        loading={isLoading}
        locale={{ emptyText: '暂无证据文件' }}
        dataSource={evidences ?? []}
        renderItem={(ev) => (
          <List.Item
            actions={[
              ev.download_url ? (
                <Button
                  size="small"
                  type="link"
                  icon={<DownloadOutlined />}
                  href={ev.download_url}
                  target="_blank"
                >
                  下载
                </Button>
              ) : null,
            ].filter(Boolean) as React.ReactNode[]}
          >
            <List.Item.Meta
              avatar={<PaperClipOutlined style={{ fontSize: 18, color: 'var(--crucible-text-disabled)' }} />}
              title={
                <Space>
                  <Text style={{ fontSize: 13 }}>{ev.file_name}</Text>
                  <Tag style={{ fontSize: 11 }}>{ev.kind}</Tag>
                </Space>
              }
              description={
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {ev.content_type} · {(ev.size_bytes / 1024).toFixed(1)} KB ·{' '}
                  {dayjs(ev.created_at).format('MM-DD HH:mm')}
                </Text>
              }
            />
          </List.Item>
        )}
      />
    </div>
  )
}

// ── 创建任务抽屉 ──

function CreateTaskDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()

  // 拉凭据列表供多选（P1-6）
  const { data: credentialsData } = useQuery({
    queryKey: ['credentials'],
    queryFn: () => api.listCredentials(),
    enabled: open,
  })

  const createMutation = useMutation({
    mutationFn: (values: {
      project_address: string
      project_ref?: string
      vulnerability_description: string
      priority: string
      credential_refs?: string[]
    }) => api.createTask(values),
    onSuccess: () => {
      message.success('任务已创建，进入分析队列')
      form.resetFields()
      onClose()
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  return (
    <Drawer open={open} onClose={onClose} width={560} title="新建漏洞验证任务">
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => createMutation.mutate(v)}
        initialValues={{ priority: 'medium', source_type: 'git' }}
      >
        <Form.Item
          name="project_address"
          label="项目地址 (Git URL)"
          rules={[{ required: true, message: '请输入项目 Git 地址' }]}
        >
          <Input placeholder="https://github.com/org/repo.git" />
        </Form.Item>
        <Form.Item name="project_ref" label="分支 / Commit / Tag">
          <Input placeholder="默认分支（留空）" />
        </Form.Item>
        <Form.Item name="priority" label="优先级">
          <Select
            options={[
              { value: 'low', label: '低' },
              { value: 'medium', label: '中' },
              { value: 'high', label: '高' },
              { value: 'critical', label: '严重' },
            ]}
          />
        </Form.Item>
        <Form.Item
          name="vulnerability_description"
          label="漏洞描述"
          rules={[{ required: true, min: 10, message: '请至少输入 10 个字符的漏洞描述' }]}
        >
          <Input.TextArea rows={5} placeholder="描述待验证的漏洞类型、疑似位置、触发条件等" />
        </Form.Item>
        <Form.Item
          name="credential_refs"
          label="关联凭据"
          extra="任务运行时注入 agent 容器（环境变量 / 密钥文件 600）。在「设置 → 任务凭据」新建。"
        >
          <Select
            mode="multiple"
            allowClear
            placeholder="无需凭据则留空"
            optionLabelProp="label"
            options={(credentialsData?.items ?? []).map((c) => ({
              value: c.id,
              label: `${c.name} (${c.kind === 'env_var' ? 'env:' : 'file:'}${c.target})`,
            }))}
          />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" loading={createMutation.isPending}>
            提交分析
          </Button>
          <Button onClick={onClose}>取消</Button>
        </Space>
      </Form>
    </Drawer>
  )
}

// ── 主页面 ──

export function TasksPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [detailTaskId, setDetailTaskId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string | undefined>()

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['tasks', statusFilter],
    queryFn: () => api.listTasks(statusFilter ? { status: statusFilter } : {}),
    refetchInterval: 5000,
  })

  const cancelMutation = useMutation({
    mutationFn: (id: string) => api.cancelTask(id),
    onSuccess: () => {
      message.success('任务已取消')
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const retryMutation = useMutation({
    mutationFn: (id: string) => api.retryTask(id),
    onSuccess: () => {
      message.success('任务已重新提交(断点续跑)')
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteTask(id),
    onSuccess: () => {
      message.success('任务已删除')
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const columns: ColumnsType<TaskSummary> = [
    {
      title: '项目地址',
      dataIndex: 'project_address',
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (v: string) => {
        const m = getStatusMeta(v)
        return <Tag color={m.color}>{m.label}</Tag>
      },
    },
    {
      title: '判定',
      dataIndex: 'verdict',
      width: 110,
      render: (v: string | null) =>
        v ? <Tag color={getVerdictMeta(v).color}>{getVerdictMeta(v).label}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 90,
      render: (v: string) => <Tag color={getPriorityMeta(v).color}>{getPriorityMeta(v).label}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_, row) => (
        <Space size="small" wrap>
          <Button size="small" onClick={() => setDetailTaskId(row.id)}>
            详情
          </Button>
          {['queued', 'running', 'pending'].includes(row.status) && (
            <Button
              size="small"
              danger
              icon={<PauseCircleOutlined />}
              onClick={() => cancelMutation.mutate(row.id)}
            >
              取消
            </Button>
          )}
          {['failed', 'cancelled', 'completed', 'needs_review'].includes(row.status) && (
            <Button
              size="small"
              icon={<RedoOutlined />}
              onClick={() => retryMutation.mutate(row.id)}
              loading={retryMutation.isPending}
            >
              重试
            </Button>
          )}
          {!['running', 'pending', 'queued'].includes(row.status) && (
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => {
                Modal.confirm({
                  title: '删除任务',
                  content: '任务及其运行记录将被归档(软删)。确定继续?',
                  okText: '删除',
                  okType: 'danger',
                  cancelText: '取消',
                  onOk: () => deleteMutation.mutate(row.id),
                })
              }}
              loading={deleteMutation.isPending}
            />
          )}
        </Space>
      ),
    },
  ]

  return (
    <AppLayout>
      <PageHeader
        title="任务管理"
        subtitle="提交漏洞验证任务，Agent 将在隔离沙箱中自动分析"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建任务
            </Button>
          </Space>
        }
      />

      <Card style={{ marginBottom: 16 }}>
        <Space>
          <Select
            allowClear
            placeholder="按状态筛选"
            style={{ width: 180 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: 'queued', label: '排队中' },
              { value: 'running', label: '分析中' },
              { value: 'needs_review', label: '待复核' },
              { value: 'completed', label: '已完成' },
              { value: 'failed', label: '失败' },
              { value: 'cancelled', label: '已取消' },
            ]}
          />
        </Space>
      </Card>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={{ pageSize: 10, total: data?.total ?? 0, showTotal: (t) => `共 ${t} 条` }}
      />

      <CreateTaskDrawer open={createOpen} onClose={() => setCreateOpen(false)} />
      <TaskDetailDrawer
        open={!!detailTaskId}
        taskId={detailTaskId}
        onClose={() => setDetailTaskId(null)}
      />
    </AppLayout>
  )
}
