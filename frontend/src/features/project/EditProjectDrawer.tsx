import { useEffect } from 'react'

import { App, Button, Drawer, Form, Input, Space } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  GIT_REF_PLACEHOLDERS,
  GitRefTypeBanners,
  type GitRefType,
} from '../../shared/components/GitRefTypeBanners'
import { api, type Project } from '../../shared/lib/api'
import {
  buildProjectUpdatePayload,
  isUploadProject,
  projectEditInitialValues,
  type ProjectEditValues,
} from './projectUpdate'

interface EditProjectDrawerProps {
  open: boolean
  project: Project | null
  onClose: () => void
}

export function EditProjectDrawer({ open, project, onClose }: EditProjectDrawerProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm<ProjectEditValues>()
  const upload = project ? isUploadProject(project) : false
  const refType = Form.useWatch('default_ref_type', form) ?? 'branch'

  useEffect(() => {
    if (!open || !project) return
    form.setFieldsValue(projectEditInitialValues(project))
  }, [open, project, form])

  const mutation = useMutation({
    mutationFn: (values: ProjectEditValues) => {
      if (!project) throw new Error('项目不存在')
      return api.updateProject(project.id, buildProjectUpdatePayload(project, values))
    },
    onSuccess: (updated) => {
      message.success('项目已更新')
      onClose()
      void qc.invalidateQueries({ queryKey: ['projects'] })
      void qc.invalidateQueries({ queryKey: ['project', updated.id] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  return (
    <Drawer open={open} onClose={onClose} size={480} title="编辑项目" destroyOnHidden>
      {project ? (
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => mutation.mutate(values)}
        >
          <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请填写名称' }]}>
            <Input placeholder="例如 claudecodeui" />
          </Form.Item>
          {upload ? null : (
            <>
              <Form.Item
                label="Git 地址"
                extra="仓库地址登记后不可更改。如需换仓库，请重新登记项目。"
              >
                <Input value={project.git_url} disabled />
              </Form.Item>
              <Form.Item
                name="default_ref_type"
                label="默认引用类型"
                rules={[{ required: true, message: '请选择引用类型' }]}
                extra="之后发起审计时可沿用此默认版本。"
              >
                <GitRefTypeBanners />
              </Form.Item>
              <Form.Item name="default_ref" label="默认引用名称">
                <Input placeholder={GIT_REF_PLACEHOLDERS[refType as GitRefType]} />
              </Form.Item>
            </>
          )}
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Space>
            <Button type="primary" htmlType="submit" loading={mutation.isPending}>
              保存
            </Button>
            <Button onClick={onClose}>取消</Button>
          </Space>
        </Form>
      ) : null}
    </Drawer>
  )
}
