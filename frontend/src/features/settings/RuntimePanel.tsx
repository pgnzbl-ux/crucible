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
        按“任务 → 线索终认 → AI 容器 → 靶场复现”分层限流。保存后会作用于新的调度和正在等待的工位，
        不会中断已经运行的容器，也没有执行总时长超时。
      </Paragraph>

      <Form form={form} layout="vertical" onFinish={(values) => mutation.mutate(values)}>
        <Row gutter={[12, 12]}>
          <BudgetField
            name="max_concurrent_tasks"
            label="同时运行任务"
            max={data?.max_allowed ?? 4}
            description="控制处于 running 的任务总数；扫描、建靶场和 AI 阶段都计入任务运行。"
          />
          <BudgetField
            name="max_concurrent_agent_runners"
            label="全局 AI 容器"
            max={data?.agent_runner_max_allowed ?? 4}
            description="所有任务共享的 Docker AI 容器总槽位，是实际资源占用的总闸门。"
          />
          <BudgetField
            name="lead_verify_per_task"
            label="单任务线索终认"
            max={Math.min(data?.lead_verify_max_allowed ?? 4, runnerCount)}
            description="同一任务可并行审计的独立线索数；每条线索保持独立容器和输入输出。"
          />
          <BudgetField
            name="reproduce_per_lab"
            label="同靶场复现"
            max={Math.min(data?.reproduce_max_allowed ?? 4, leadCount)}
            description="同一靶场可同时执行的动态复现数。建议保持 1，避免状态和数据相互污染。"
          />
        </Row>

        <Alert
          style={{ marginTop: 16 }}
          type={theoreticalDemand > runnerCount ? 'info' : 'success'}
          showIcon
          message={
            <Space wrap>
              <span>理论终认需求 {theoreticalDemand} 个容器</span>
              <span>实际最多 {Math.min(theoreticalDemand, runnerCount)} 个</span>
              <Tag color="blue">全局槽 {runnerCount}</Tag>
            </Space>
          }
          description="超过全局 AI 容器上限的线索会等待槽位，不会失败，也不会因等待被标记超时。"
        />

        <Space style={{ marginTop: 16 }}>
          <Button type="primary" htmlType="submit" loading={mutation.isPending}>
            保存设置
          </Button>
          {data && <Text type="secondary">部署硬顶 {data.agent_runner_max_allowed}</Text>}
        </Space>
      </Form>
    </Card>
  )
}
