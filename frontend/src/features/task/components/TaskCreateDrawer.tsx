import { useEffect } from 'react'
import { App, AutoComplete, Button, Drawer, Form, Input, Select, Space } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'wouter'

import { api } from '../../../shared/lib/api'

interface TaskCreateDrawerProps {
  open: boolean
  onClose: () => void
  initialValues?: {
    project_address?: string
    project_ref?: string
  }
}

export function TaskCreateDrawer({ open, onClose, initialValues }: TaskCreateDrawerProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const [, navigate] = useLocation()

  const { data: credentialsData } = useQuery({
    queryKey: ['credentials'],
    queryFn: () => api.listCredentials(),
    enabled: open,
  })

  const { data: projectsData } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.listProjects({ limit: '100' }),
    enabled: open,
  })

  useEffect(() => {
    if (!open || !initialValues?.project_address) return
    form.setFieldsValue({
      project_address: initialValues.project_address,
      project_ref: initialValues.project_ref,
    })
  }, [open, form, initialValues?.project_address, initialValues?.project_ref])

  const createMutation = useMutation({
    mutationFn: (values: {
      project_address: string
      project_ref?: string
      vulnerability_description: string
      priority: string
      credential_refs?: string[]
    }) => api.createTask(values),
    onSuccess: (task) => {
      message.success('任务已创建，正在进入分析')
      form.resetFields()
      onClose()
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/tasks/${task.id}?tab=progress`)
    },
    onError: (e: Error) => message.error(e.message),
  })

  const projectOptions = (projectsData?.items ?? []).map((p) => ({
    value: p.git_url,
    label: `${p.name}  ${p.git_url}`,
  }))

  return (
    <Drawer open={open} onClose={onClose} size={560} title="新建漏洞验证任务">
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => createMutation.mutate(v)}
        initialValues={{ priority: 'medium' }}
      >
        <Form.Item
          name="project_address"
          label="项目地址 (Git URL)"
          rules={[{ required: true, message: '请输入项目 Git 地址' }]}
          extra="可直接粘贴，或从已登记仓库里选。同一地址会复用源码缓存。"
        >
          <AutoComplete
            options={projectOptions}
            placeholder="https://github.com/org/repo.git"
            onSelect={(url) => {
              const hit = (projectsData?.items ?? []).find((p) => p.git_url === url)
              if (hit?.default_ref) form.setFieldValue('project_ref', hit.default_ref)
            }}
          >
            <Input />
          </AutoComplete>
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
