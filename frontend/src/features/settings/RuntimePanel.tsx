import { useEffect } from 'react'
import { App, Button, Card, Form, InputNumber, Space, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../shared/lib/api'
import { useErrorToast } from '../../shared/hooks/useErrorToast'

const { Text, Paragraph } = Typography

export function RuntimePanel() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm<{ max_concurrent_tasks: number }>()

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['runtime-settings'],
    queryFn: () => api.getRuntimeSettings(),
  })
  useErrorToast(isError, error, '运行设置加载失败')

  useEffect(() => {
    if (!data) return
    form.setFieldsValue({ max_concurrent_tasks: data.max_concurrent_tasks })
  }, [data, form])

  const mutation = useMutation({
    mutationFn: (n: number) => api.updateRuntimeSettings({ max_concurrent_tasks: n }),
    onSuccess: (saved) => {
      message.success('已保存同时运行上限')
      qc.setQueryData(['runtime-settings'], saved)
    },
    onError: (e: Error) => message.error(e.message),
  })

  return (
    <Card loading={isLoading}>
      <Paragraph type="secondary">
        同时处于 running 的验证任务上限。改完立即作用于排队中的任务，不会中断已经在跑的任务。
      </Paragraph>
      <Form
        form={form}
        layout="inline"
        onFinish={(values) => mutation.mutate(values.max_concurrent_tasks)}
      >
        <Form.Item
          name="max_concurrent_tasks"
          label="同时运行任务数"
          rules={[{ required: true, type: 'number' }]}
        >
          <InputNumber min={1} max={data?.max_allowed ?? 4} />
        </Form.Item>
        <Form.Item>
          <Space>
            <Button type="primary" htmlType="submit" loading={mutation.isPending}>
              保存
            </Button>
            {data && <Text type="secondary">硬顶 {data.max_allowed}</Text>}
          </Space>
        </Form.Item>
      </Form>
    </Card>
  )
}
