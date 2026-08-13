import { App, Button, Drawer, Form, Input, Select, Space } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../../shared/lib/api'

interface TaskCreateDrawerProps {
  open: boolean
  onClose: () => void
}

export function TaskCreateDrawer({ open, onClose }: TaskCreateDrawerProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()

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
          extra="任务运行时注入 agent 容器。在「设置 → 任务凭据」新建。"
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
