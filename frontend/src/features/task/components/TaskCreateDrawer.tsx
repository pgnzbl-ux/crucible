import { useEffect, useMemo } from 'react'

import { App, AutoComplete, Button, Drawer, Form, Input, Radio, Select, Space, Upload } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'wouter'

import {
  GIT_REF_PLACEHOLDERS,
  GitRefTypeBanners,
  type GitRefType,
} from '../../../shared/components/GitRefTypeBanners'
import { api } from '../../../shared/lib/api'
import { tryLockTaskAction, unlockTaskAction } from '../../../shared/lib/taskActionLock'
import { TASK_CREATE_LOCK_ID } from '../../../shared/lib/taskCache'
import {
  buildGitProjectOptions,
  buildProjectVersionOptions,
  filterGitProjectOption,
  matchProjectVersionKey,
  parseProjectVersionKey,
  type GitProjectOption,
  type ProjectSelectSource,
} from '../lib/projectSelectOptions'
import type { SourceArtifact } from '../../../shared/lib/api'

const ARCHIVE_ACCEPT = '.zip,.tar,.tar.gz,.tgz,application/zip,application/gzip,application/x-tar'

/** 从项目资产详情页进入：锁定项目，版本只能从下拉选 */
export type BoundProjectSource = ProjectSelectSource & {
  source_type?: 'git' | 'local_upload' | string
  artifacts?: SourceArtifact[]
}

interface TaskCreateDrawerProps {
  open: boolean
  onClose: () => void
  initialValues?: {
    project_address?: string
    project_ref?: string
    project_ref_type?: GitRefType
    clone_depth?: number
    source_type?: 'git' | 'local_upload'
  }
  boundProject?: BoundProjectSource
}

type CreateFormValues = {
  source_type?: 'git' | 'local_upload'
  project_address?: string
  project_version_key?: string
  project_ref?: string
  project_ref_type?: GitRefType
  clone_depth?: number
  task_type?: 'verify' | 'discovery'
  vulnerability_description?: string
  priority: string
  credential_refs?: string[]
  existing_upload_url?: string
  upload_project_name?: string
  archive?: { originFileObj?: File }[]
}

export function TaskCreateDrawer({
  open,
  onClose,
  initialValues,
  boundProject,
}: TaskCreateDrawerProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm<CreateFormValues>()
  const [, navigate] = useLocation()
  const refType = Form.useWatch('project_ref_type', form)
  const sourceType = Form.useWatch('source_type', form) ?? 'git'
  const existingUpload = Form.useWatch('existing_upload_url', form)
  const sourceLocked = Boolean(boundProject ?? initialValues?.project_address)

  const boundVersionOptions = useMemo(
    () => (boundProject ? buildProjectVersionOptions(boundProject, boundProject.artifacts) : []),
    [boundProject],
  )

  const applyVersionKey = (key: string | undefined) => {
    if (!key) return
    const parsed = parseProjectVersionKey(key)
    form.setFieldsValue({
      project_version_key: key,
      project_ref_type: parsed.ref_type,
      project_ref: parsed.ref_name,
    })
  }

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
    const fromBound = boundProject
    const source_type =
      fromBound?.source_type === 'local_upload' || initialValues?.source_type === 'local_upload'
        ? 'local_upload'
        : 'git'
    const project_address = fromBound?.git_url ?? initialValues?.project_address
    const versionKey = fromBound
      ? matchProjectVersionKey(
          buildProjectVersionOptions(fromBound, fromBound.artifacts),
          initialValues?.project_ref_type ?? 'branch',
          initialValues?.project_ref ?? fromBound.default_ref,
        )
      : undefined
    const parsed = versionKey ? parseProjectVersionKey(versionKey) : null
    form.setFieldsValue({
      source_type,
      project_address,
      project_version_key: versionKey,
      project_ref: parsed?.ref_name ?? initialValues?.project_ref,
      project_ref_type:
        parsed?.ref_type ??
        initialValues?.project_ref_type ??
        (source_type === 'git' ? 'branch' : undefined),
      clone_depth: initialValues?.clone_depth ?? 1,
      existing_upload_url: source_type === 'local_upload' ? project_address : undefined,
    })
  }, [
    open,
    form,
    boundProject,
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
      const taskType: 'verify' | 'discovery' = values.task_type === 'verify' ? 'verify' : 'discovery'
      const description = values.vulnerability_description
      const priority = values.priority
      const credential_refs = values.credential_refs
      if (source_type === 'local_upload') {
        const existing = values.existing_upload_url
        if (existing) {
          return api.createTask({
            project_address: existing,
            source_type: 'local_upload',
            task_type: taskType,
            vulnerability_description: taskType === 'verify' ? description : undefined,
            priority,
            credential_refs,
          })
        }
        const file = values.archive?.[0]?.originFileObj
        if (file) {
          const uploadName = values.upload_project_name?.trim()
          if (!uploadName) {
            throw new Error('上传新源码包时请填写项目名称')
          }
          return api.createTaskFromUpload({
            file,
            name: uploadName,
            task_type: taskType,
            vulnerability_description: taskType === 'verify' ? description : undefined,
            priority,
            credential_refs,
          })
        }
        throw new Error('请上传源码包，或选择已入库的上传项目')
      }
      const payload = {
        project_address: values.project_address || '',
        task_type: taskType,
        project_ref: values.project_ref,
        project_ref_type: values.project_ref_type,
        clone_depth: values.clone_depth,
        vulnerability_description: taskType === 'discovery' ? undefined : description,
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

  const projectOptions = buildGitProjectOptions(gitProjects)

  const refPlaceholder =
    (refType && GIT_REF_PLACEHOLDERS[refType as GitRefType]) ||
    GIT_REF_PLACEHOLDERS.branch

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={560}
      title={boundProject ? `发起代码审计 · ${boundProject.name}` : '发起代码审计'}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => {
          if (!tryLockTaskAction(TASK_CREATE_LOCK_ID)) return
          createMutation.mutate(v)
        }}
        initialValues={{
          priority: 'medium',
          clone_depth: 1,
          source_type: 'git',
          project_ref_type: 'branch',
        }}
      >
        <Form.Item name="source_type" label="源码来源" hidden={sourceLocked}>
          <Radio.Group
            options={[
              { value: 'git', label: 'Git 仓库' },
              { value: 'local_upload', label: '上传源码包' },
            ]}
          />
        </Form.Item>
        {sourceLocked ? (
          <>
            <Form.Item label={boundProject?.source_type === 'local_upload' ? '已绑定上传项目' : '已绑定 Git 项目'}>
              <Input
                value={
                  boundProject
                    ? `${boundProject.name}  ${boundProject.git_url}`
                    : initialValues?.project_address
                }
                disabled
              />
            </Form.Item>
            <Form.Item name="project_address" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="existing_upload_url" hidden>
              <Input />
            </Form.Item>
          </>
        ) : null}

        {!sourceLocked && sourceType === 'git' ? (
          <Form.Item
            name="project_address"
            label="项目地址 (Git URL)"
            rules={[{ required: true, message: '请输入项目 Git 地址' }]}
            extra="可直接粘贴，或从已登记仓库里选。选项格式：项目名称：tag/commit/branch  <Git 地址>。"
            getValueFromEvent={(value) => {
              const hit = projectOptions.find((o) => o.value === value)
              return hit?.git_url ?? value
            }}
          >
            <AutoComplete
              options={projectOptions}
              placeholder="https://github.com/org/repo.git"
              filterOption={filterGitProjectOption}
              onSelect={(_value, option) => {
                const hit = option as GitProjectOption
                form.setFieldsValue({
                  project_ref_type: hit.ref_type,
                  project_ref: hit.ref_name ?? '',
                })
              }}
            >
              <Input />
            </AutoComplete>
          </Form.Item>
        ) : null}

        {sourceType === 'git' ? (
          boundProject ? (
            <>
              <Form.Item
                name="project_version_key"
                label="源码版本"
                rules={[{ required: true, message: '请选择源码版本' }]}
                extra="仅可从该项目已登记或已缓存的版本中选择，不可手改引用。"
              >
                <Select
                  options={boundVersionOptions}
                  onChange={applyVersionKey}
                  showSearch={boundVersionOptions.length > 6}
                  optionFilterProp="label"
                />
              </Form.Item>
              <Form.Item name="project_ref_type" hidden>
                <Input />
              </Form.Item>
              <Form.Item name="project_ref" hidden>
                <Input />
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
                name="project_ref_type"
                label="引用类型"
                rules={[{ required: true, message: '请选择引用类型' }]}
                extra="点选分支 / 标签 / 提交，避免平台自动推断误判（如禅道发行 tag）。"
              >
                <GitRefTypeBanners />
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
          )
        ) : !sourceLocked ? (
          <>
            {!existingUpload ? (
              <Form.Item
                name="upload_project_name"
                label="项目名称"
                extra="同一账号下名称不能重复；与项目资产登记规则一致。"
                rules={[{ required: true, message: '请填写项目名称' }]}
              >
                <Input placeholder="例如 demo-app" />
              </Form.Item>
            ) : null}
            <Form.Item
              name="archive"
              label="源码包"
              valuePropName="fileList"
              getValueFromEvent={(e) => (Array.isArray(e) ? e : e?.fileList)}
              extra={
                existingUpload
                  ? '已选择下方入库项目，无需再上传文件。'
                  : '支持 zip / tar / tar.gz，不超过 200MB。同名项目会拒绝；建议先在项目资产登记。'
              }
              rules={[
                {
                  validator: async (_, fileList) => {
                    const hasFile = Array.isArray(fileList) && fileList[0]?.originFileObj
                    const existing = form.getFieldValue('existing_upload_url')
                    if (hasFile || existing || sourceLocked) return
                    throw new Error('请上传源码包，或选择已入库的上传项目')
                  },
                },
              ]}
            >
              <Upload.Dragger
                maxCount={1}
                accept={ARCHIVE_ACCEPT}
                beforeUpload={() => false}
                disabled={Boolean(existingUpload)}
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
                extra="选择已登记的上传项目。同名不能重复登记，请换名称。"
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
        ) : null}

        <Form.Item
          name="task_type"
          label="分析方式"
          initialValue="discovery"
          extra="代码审计用于自动挖掘漏洞；定向验证用于验证你已经掌握的具体线索"
        >
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            options={[
              { value: 'discovery', label: '代码审计' },
              { value: 'verify', label: '定向验证' },
            ]}
          />
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
        <Form.Item noStyle shouldUpdate={(a, b) => a.task_type !== b.task_type}>
          {({ getFieldValue }) => {
            const taskType = getFieldValue('task_type') ?? 'discovery'
            return (
              <Form.Item
                name="vulnerability_description"
                label="已知漏洞线索"
                rules={
                  taskType === 'verify'
                    ? [{ required: true, min: 10, message: '请至少输入 10 个字符的漏洞描述' }]
                    : []
                }
                extra={
                  taskType === 'discovery'
                    ? '代码审计会通过扫描引擎与 AI 二审自动发现线索'
                    : undefined
                }
              >
                <Input.TextArea
                  rows={5}
                  disabled={taskType === 'discovery'}
                  placeholder={
                    taskType === 'discovery'
                      ? '无需填写；系统将自动分析整个代码版本'
                      : '描述待验证的漏洞类型、疑似位置、触发条件等'
                  }
                />
              </Form.Item>
            )
          }}
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
            开始分析
          </Button>
          <Button onClick={onClose}>取消</Button>
        </Space>
      </Form>
    </Drawer>
  )
}
