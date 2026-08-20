import { useEffect } from 'react'

import { App, AutoComplete, Button, Drawer, Form, Input, Radio, Select, Space, Upload } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'wouter'

import { api, type Project } from '../../../shared/lib/api'
import { tryLockTaskAction, unlockTaskAction } from '../../../shared/lib/taskActionLock'
import { TASK_CREATE_LOCK_ID } from '../../../shared/lib/taskCache'

const REF_PLACEHOLDERS: Record<string, string> = {
  branch: 'main / master / develop（留空=默认分支）',
  tag: 'v1.0.0 / zentaopms_22.4_20260730',
  commit: '完整或短 SHA（7–40 位十六进制）',
}

const ARCHIVE_ACCEPT = '.zip,.tar,.tar.gz,.tgz,application/zip,application/gzip,application/x-tar'

interface TaskCreateDrawerProps {
  open: boolean
  onClose: () => void
  initialValues?: {
    project_address?: string
    project_ref?: string
    project_ref_type?: 'branch' | 'tag' | 'commit'
    clone_depth?: number
    source_type?: 'git' | 'local_upload'
  }
}

type CreateFormValues = {
  source_type?: 'git' | 'local_upload'
  project_address?: string
  project_ref?: string
  project_ref_type?: 'branch' | 'tag' | 'commit'
  clone_depth?: number
  vulnerability_description: string
  priority: string
  credential_refs?: string[]
  existing_upload_url?: string
  archive?: { originFileObj?: File }[]
}

export function TaskCreateDrawer({ open, onClose, initialValues }: TaskCreateDrawerProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm<CreateFormValues>()
  const [, navigate] = useLocation()
  const refType = Form.useWatch('project_ref_type', form)
  const sourceType = Form.useWatch('source_type', form) ?? 'git'

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
    if (!open) return
    const source_type = initialValues?.source_type === 'local_upload' ? 'local_upload' : 'git'
    form.setFieldsValue({
      source_type,
      project_address: initialValues?.project_address,
      project_ref: initialValues?.project_ref,
      project_ref_type: initialValues?.project_ref_type,
      clone_depth: initialValues?.clone_depth ?? 1,
      existing_upload_url:
        source_type === 'local_upload' ? initialValues?.project_address : undefined,
    })
  }, [
    open,
    form,
    initialValues?.project_address,
    initialValues?.project_ref,
    initialValues?.project_ref_type,
    initialValues?.clone_depth,
    initialValues?.source_type,
  ])

  const afterCreated = (task: { id: string }) => {
    message.success('任务已创建，正在进入分析')
    form.resetFields()
    onClose()
    qc.invalidateQueries({ queryKey: ['tasks'] })
    qc.invalidateQueries({ queryKey: ['task-stats'] })
    qc.invalidateQueries({ queryKey: ['projects'] })
    navigate(`/tasks/${task.id}?tab=progress`)
  }

  const createMutation = useMutation({
    mutationFn: async (values: CreateFormValues) => {
      const source_type = values.source_type === 'local_upload' ? 'local_upload' : 'git'
      const description = values.vulnerability_description
      const priority = values.priority
      const credential_refs = values.credential_refs
      if (source_type === 'local_upload') {
        const file = values.archive?.[0]?.originFileObj
        const existing = values.existing_upload_url
        if (file) {
          return api.createTaskFromUpload({
            file,
            vulnerability_description: description,
            priority,
            credential_refs,
          })
        }
        if (existing) {
          return api.createTask({
            project_address: existing,
            source_type: 'local_upload',
            vulnerability_description: description,
            priority,
            credential_refs,
          })
        }
        throw new Error('请上传源码包，或选择已入库的上传项目')
      }
      const payload = {
        project_address: values.project_address || '',
        project_ref: values.project_ref,
        project_ref_type: values.project_ref_type,
        clone_depth: values.clone_depth,
        vulnerability_description: description,
        priority,
        credential_refs,
      }
      if (!payload.project_ref_type) delete payload.project_ref_type
      return api.createTask(payload)
    },
    onSuccess: afterCreated,
    onError: (e: Error) => message.error(e.message),
    onSettled: () => unlockTaskAction(TASK_CREATE_LOCK_ID),
  })

  const gitProjects = (projectsData?.items ?? []).filter((p) => p.source_type !== 'local_upload')
  const uploadProjects = (projectsData?.items ?? []).filter((p) => p.source_type === 'local_upload')

  const projectOptions = gitProjects.map((p: Project) => ({
    value: p.git_url,
    label: `${p.name}  ${p.git_url}`,
  }))

  const refPlaceholder =
    REF_PLACEHOLDERS[refType as string] ?? '默认分支（留空）；可下方选择引用类型'

  return (
    <Drawer open={open} onClose={onClose} size={560} title="新建漏洞验证任务">
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => {
          if (!tryLockTaskAction(TASK_CREATE_LOCK_ID)) return
          createMutation.mutate(v)
        }}
        initialValues={{ priority: 'medium', clone_depth: 1, source_type: 'git' }}
      >
        <Form.Item name="source_type" label="源码来源">
          <Radio.Group
            options={[
              { value: 'git', label: 'Git 仓库' },
              { value: 'local_upload', label: '上传源码包' },
            ]}
          />
        </Form.Item>

        {sourceType === 'git' ? (
          <>
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
                  const hit = gitProjects.find((p) => p.git_url === url)
                  if (hit?.default_ref) form.setFieldValue('project_ref', hit.default_ref)
                }}
              >
                <Input />
              </AutoComplete>
            </Form.Item>
            <Form.Item
              name="project_ref_type"
              label="引用类型"
              extra="明确指定 branch / tag / commit，避免平台自动推断误判（如禅道发行 tag）。"
            >
              <Select
                allowClear
                placeholder="自动推断（默认）"
                options={[
                  { value: 'branch', label: '分支 (branch)' },
                  { value: 'tag', label: '标签 (tag)' },
                  { value: 'commit', label: '提交 (commit)' },
                ]}
              />
            </Form.Item>
            <Form.Item name="project_ref" label="引用名称">
              <Input placeholder={refPlaceholder} />
            </Form.Item>
            <Form.Item
              name="clone_depth"
              label="克隆深度"
              extra="浅克隆可减少流量；深度 0 为全量 clone（历史完整，流量更大）。"
            >
              <Select
                options={[
                  { value: 1, label: '1（默认，最省流量）' },
                  { value: 5, label: '5' },
                  { value: 10, label: '10' },
                  { value: 50, label: '50' },
                  { value: 0, label: '0 — 全量 clone' },
                ]}
              />
            </Form.Item>
          </>
        ) : (
          <>
            <Form.Item
              name="archive"
              label="源码包"
              valuePropName="fileList"
              getValueFromEvent={(e) => (Array.isArray(e) ? e : e?.fileList)}
              extra="支持 zip / tar / tar.gz，不超过 200MB。单层目录会作为项目根。"
              rules={[
                {
                  validator: async (_, fileList) => {
                    const hasFile = Array.isArray(fileList) && fileList[0]?.originFileObj
                    const existing = form.getFieldValue('existing_upload_url')
                    if (hasFile || existing) return
                    throw new Error('请上传源码包，或选择已入库的上传项目')
                  },
                },
              ]}
            >
              <Upload.Dragger
                maxCount={1}
                accept={ARCHIVE_ACCEPT}
                beforeUpload={() => false}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">点击或拖拽源码包到这里</p>
                <p className="ant-upload-hint">zip / tar / tar.gz，最大 200MB</p>
              </Upload.Dragger>
            </Form.Item>
            {uploadProjects.length > 0 ? (
              <Form.Item
                name="existing_upload_url"
                label="或选择已上传项目"
                extra="同一内容指纹会复用 MinIO 里的源码包。"
              >
                <Select
                  allowClear
                  placeholder="不选则使用上方新上传的文件"
                  options={uploadProjects.map((p) => ({
                    value: p.git_url,
                    label: `${p.name}  ${p.git_url}`,
                  }))}
                />
              </Form.Item>
            ) : null}
          </>
        )}

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
