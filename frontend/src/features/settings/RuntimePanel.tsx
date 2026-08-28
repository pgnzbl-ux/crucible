import { useEffect } from 'react'
import { Alert, App, Button, Card, Col, Form, InputNumber, Row, Space, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, type RuntimeSettingsInput } from '../../shared/lib/api'
import { useErrorToast } from '../../shared/hooks/useErrorToast'

const { Text, Paragraph, Title } = Typography

type BudgetFieldProps = {
  name: keyof RuntimeSettingsInput
  label: string
  description: string
  max: number
}

function BudgetField({ name, label, description, max }: BudgetFieldProps) {
  return (
    <Col xs={24} md={12}>
      <Card size="small" style={{ height: '100%' }}>
        <Form.Item
          name={name}
          label={label}
          rules={[
            { required: true, message: `请设置${label}` },
            { type: 'number', min: 1, max, message: `范围为 1–${max}` },
          ]}
          style={{ marginBottom: 8 }}
        >
          <InputNumber min={1} max={max} precision={0} style={{ width: '100%' }} />
        </Form.Item>
        <Text type="secondary">{description}</Text>
      </Card>
    </Col>
  )
}

type DurationFieldProps = {
  name: keyof RuntimeSettingsInput
  label: string
  description: string
  /** 表单字段实际以「分钟」为单位编辑，提交前换算回秒 */
  form: ReturnType<typeof Form.useForm<RuntimeSettingsInput>>[0]
}

const MINUTES_MAX = 7 * 24 * 60 // 与后端上界 7 天一致

type SecondsFieldProps = {
  name: keyof RuntimeSettingsInput
  label: string
  description: string
  min: number
  max: number
}

/** 靶场搭建时序字段：以「秒」为单位编辑，始终为正（无 0=不限语义） */
function SecondsField({ name, label, description, min, max }: SecondsFieldProps) {
  return (
    <Col xs={24} md={12}>
      <Card size="small" style={{ height: '100%' }}>
        <Form.Item
          name={name}
          label={label}
          rules={[
            { required: true, message: `请设置${label}` },
            { type: 'number', min, max, message: `范围为 ${min}–${max} 秒` },
          ]}
          style={{ marginBottom: 8 }}
        >
          <InputNumber min={min} max={max} precision={0} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>
        <Text type="secondary">{description}</Text>
      </Card>
    </Col>
  )
}

function DurationField({ name, label, description, form }: DurationFieldProps) {
  const seconds = Form.useWatch(name as string, form) as number | undefined
  return (
    <Col xs={24} md={12}>
      <Card size="small" style={{ height: '100%' }}>
        <Form.Item
          name={name}
          label={label}
          rules={[
            { required: true, message: `请设置${label}` },
            { type: 'number', min: 0, max: MINUTES_MAX, message: `范围为 0–${MINUTES_MAX} 分钟` },
          ]}
          style={{ marginBottom: 8 }}
        >
          <InputNumber
            min={0}
            max={MINUTES_MAX}
            precision={0}
            style={{ width: '100%' }}
            addonAfter="分钟"
          />
        </Form.Item>
        <Text type="secondary">
          {description}
          {seconds === 0 ? '（当前：不限）' : ''}
        </Text>
      </Card>
    </Col>
  )
}

export function RuntimePanel() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm<RuntimeSettingsInput>()
  const watched = Form.useWatch([], form)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['runtime-settings'],
    queryFn: () => api.getRuntimeSettings(),
  })
  useErrorToast(isError, error, '运行设置加载失败')

  useEffect(() => {
    if (!data) return
    form.setFieldsValue({
      max_concurrent_tasks: data.max_concurrent_tasks,
      max_concurrent_agent_runners: data.max_concurrent_agent_runners,
      lead_verify_per_task: data.lead_verify_per_task,
      reproduce_per_lab: data.reproduce_per_lab,
      task_time_budget_seconds: Math.round(data.task_time_budget_seconds / 60),
      ai_node_timeout_seconds: Math.round(data.ai_node_timeout_seconds / 60),
      env_ready_max_attempts: data.env_ready_max_attempts,
      env_ready_compose_up_timeout_seconds: data.env_ready_compose_up_timeout_seconds,
      env_ready_compose_wait_seconds: data.env_ready_compose_wait_seconds,
      env_ready_lab_wait_timeout_seconds: data.env_ready_lab_wait_timeout_seconds,
      env_ready_probe_window_seconds: data.env_ready_probe_window_seconds,
    })
  }, [data, form])

  const mutation = useMutation({
    mutationFn: (values: RuntimeSettingsInput) =>
      api.updateRuntimeSettings({
        ...values,
        // 表单以分钟编辑，提交换算回秒
        task_time_budget_seconds: values.task_time_budget_seconds * 60,
        ai_node_timeout_seconds: values.ai_node_timeout_seconds * 60,
      }),
    onSuccess: (saved) => {
      message.success('并发与资源设置已生效')
      qc.setQueryData(['runtime-settings'], saved)
    },
    onError: (e: Error) => message.error(e.message),
  })

  const taskCount = watched?.max_concurrent_tasks ?? data?.max_concurrent_tasks ?? 1
  const leadCount = watched?.lead_verify_per_task ?? data?.lead_verify_per_task ?? 1
  const runnerCount = watched?.max_concurrent_agent_runners ?? data?.max_concurrent_agent_runners ?? 1
  const theoreticalDemand = taskCount * leadCount

  return (
    <Card loading={isLoading}>
      <Title level={5} style={{ marginTop: 0 }}>并发与资源</Title>
      <Paragraph type="secondary">
        按任务、线索终认、AI 容器、靶场复现分层控制并发。保存后对新调度与排队中的任务生效，不会中断已在运行的容器。
      </Paragraph>

      <Form
        form={form}
        layout="vertical"
        onFinish={(values) => {
          const budgetMin = values.task_time_budget_seconds ?? 0
          const nodeTimeoutMin = values.ai_node_timeout_seconds ?? 0
          if (budgetMin > 0 && nodeTimeoutMin > budgetMin) {
            message.error('单节点超时不能大于任务总时长预算')
            return
          }
          const upSec = values.env_ready_compose_up_timeout_seconds ?? 0
          const waitSec = values.env_ready_compose_wait_seconds ?? 0
          if (waitSec > upSec) {
            message.error('compose 等待 healthy 不能大于单轮 compose up 硬超时')
            return
          }
          mutation.mutate(values)
        }}
      >
        <Row gutter={[12, 12]}>
          <BudgetField
            name="max_concurrent_tasks"
            label="同时运行任务"
            max={data?.max_allowed ?? 4}
            description="处于进行中的任务上限；扫描、建靶场与 AI 分析均计入。"
          />
          <BudgetField
            name="max_concurrent_agent_runners"
            label="全局 AI 容器"
            max={data?.agent_runner_max_allowed ?? 4}
            description="全部任务共用的 AI 分析容器数量上限。"
          />
          <BudgetField
            name="lead_verify_per_task"
            label="单任务线索终认"
            max={Math.min(data?.lead_verify_max_allowed ?? 4, runnerCount)}
            description="同一任务可并行处理的线索数；每条线索独立运行。"
          />
          <BudgetField
            name="reproduce_per_lab"
            label="同靶场复现"
            max={Math.min(data?.reproduce_max_allowed ?? 4, leadCount)}
            description="同一靶场可同时进行的动态复现数。建议保持 1，避免相互干扰。"
          />
          <DurationField
            name="task_time_budget_seconds"
            label="任务总时长预算"
            form={form}
            description="单次运行的最长执行时间，超时未完成的运行将自动终止并保留已完成节点。设为 0 不限。注意：超过部署级 Celery 软限（默认 210 分钟）仍会被兜底拦截。"
          />
          <DurationField
            name="ai_node_timeout_seconds"
            label="单节点最长执行"
            form={form}
            description="单个 AI 节点从拿到容器起计的执行上限，超时会终止该容器并把节点记为失败；不能大于任务总时长预算。设为 0 不限。"
          />
          <SecondsField
            name="env_ready_max_attempts"
            label="靶场排障轮数"
            description="靶场搭建失败后 AI 排障重试的轮数上限。"
            min={1}
            max={10}
          />
          <SecondsField
            name="env_ready_compose_up_timeout_seconds"
            label="靶场 compose up 超时"
            description="单轮 docker compose up -d --build 的硬超时。"
            min={60}
            max={7200}
          />
          <SecondsField
            name="env_ready_compose_wait_seconds"
            label="靶场 healthy 等待"
            description="compose 等待容器 healthy 的上限；重应用（Spring/Java 首启建库）建议调大。"
            min={30}
            max={3600}
          />
          <SecondsField
            name="env_ready_probe_window_seconds"
            label="靶场探活窗口"
            description="compose up 之后平台对应用入口的 HTTP 探活窗口。"
            min={30}
            max={1800}
          />
          <SecondsField
            name="env_ready_lab_wait_timeout_seconds"
            label="靶场排队等待"
            description="等待其他任务把共享靶场搭好的上限，超时后任务失败可重试。"
            min={60}
            max={14400}
          />
        </Row>

        <Alert
          style={{ marginTop: 16 }}
          type={theoreticalDemand > runnerCount ? 'info' : 'success'}
          showIcon
          message={
            <Space wrap>
              <span>预估终认需求 {theoreticalDemand} 个容器</span>
              <span>当前最多并行 {Math.min(theoreticalDemand, runnerCount)} 个</span>
              <Tag color="blue">全局上限 {runnerCount}</Tag>
            </Space>
          }
          description="超出全局 AI 容器上限的线索会排队等待，不会因此失败或超时。"
        />

        <Space style={{ marginTop: 16 }}>
          <Button type="primary" htmlType="submit" loading={mutation.isPending}>
            保存设置
          </Button>
          {data && <Text type="secondary">系统上限 {data.agent_runner_max_allowed}</Text>}
        </Space>
      </Form>
    </Card>
  )
}
