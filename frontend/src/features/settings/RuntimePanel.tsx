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
    })
  }, [data, form])

  const mutation = useMutation({
    mutationFn: (values: RuntimeSettingsInput) => api.updateRuntimeSettings(values),
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

      <Form form={form} layout="vertical" onFinish={(values) => mutation.mutate(values)}>
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
