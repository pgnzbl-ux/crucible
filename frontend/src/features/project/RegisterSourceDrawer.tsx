import { useEffect } from 'react'

import { App, Button, Drawer, Form, Input, Space, Upload } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  GIT_REF_PLACEHOLDERS,
  GitRefTypeBanners,
  type GitRefType,
} from '../../shared/components/GitRefTypeBanners'
import { api } from '../../shared/lib/api'

const ARCHIVE_ACCEPT = '.zip,.tar,.tar.gz,.tgz,application/zip,application/gzip,application/x-tar'

type Mode = 'git' | 'upload'

interface RegisterSourceDrawerProps {
  open: boolean
  mode: Mode
  onClose: () => void
}

type GitForm = {
  name: string
  git_url: string
  default_ref_type: GitRefType
  default_ref?: string
  description?: string
}

type UploadForm = {
  name: string
  description?: string
  archive?: { originFileObj?: File }[]
}

export function RegisterSourceDrawer({ open, mode, onClose }: RegisterSourceDrawerProps) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [gitForm] = Form.useForm<GitForm>()
  const [uploadForm] = Form.useForm<UploadForm>()
  const refType = Form.useWatch('default_ref_type', gitForm) ?? 'branch'

  useEffect(() => {
    if (!open) return
    gitForm.resetFields()
    uploadForm.resetFields()
  }, [open, gitForm, uploadForm])

  const gitMutation = useMutation({
    mutationFn: (values: GitForm) =>
      api.createProject({
        name: values.name,
        git_url: values.git_url,
        default_ref: values.default_ref,
        default_ref_type: values.default_ref_type,
        description: values.description,
      }),
    onSuccess: () => {
      message.success('Git 仓库已登记')
      gitForm.resetFields()
      onClose()
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const uploadMutation = useMutation({
    mutationFn: (values: UploadForm) => {
      const file = values.archive?.[0]?.originFileObj
      if (!file) throw new Error('请选择源码包')
      return api.uploadProject({
        file,
        name: values.name,
        description: values.description,
      })
    },
    onSuccess: () => {
      message.success('源码包已登记')
      uploadForm.resetFields()
      onClose()
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const pending = gitMutation.isPending || uploadMutation.isPending

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={480}
      title={mode === 'git' ? '登记 Git 仓库' : '上传源码包'}
    >
      {mode === 'git' ? (
        <Form
          form={gitForm}
          layout="vertical"
          onFinish={(v) => gitMutation.mutate(v)}
          initialValues={{ default_ref_type: 'branch' }}
        >
          <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请填写名称' }]}>
            <Input placeholder="例如 claudecodeui" />
          </Form.Item>
          <Form.Item
            name="git_url"
            label="Git 地址"
            rules={[{ required: true, message: '请填写仓库地址' }]}
          >
            <Input placeholder="https://github.com/org/repo.git" />
          </Form.Item>
          <Form.Item
            name="default_ref_type"
            label="默认引用类型"
            rules={[{ required: true, message: '请选择引用类型' }]}
            extra="选择默认的分支、标签或提交；之后发起审计时可沿用。"
          >
            <GitRefTypeBanners />
          </Form.Item>
          <Form.Item name="default_ref" label="默认引用名称">
            <Input placeholder={GIT_REF_PLACEHOLDERS[refType as GitRefType]} />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Space>
            <Button type="primary" htmlType="submit" loading={pending}>
              登记
            </Button>
            <Button onClick={onClose}>取消</Button>
          </Space>
        </Form>
      ) : (
        <Form form={uploadForm} layout="vertical" onFinish={(v) => uploadMutation.mutate(v)}>
          <Form.Item
            name="name"
            label="项目名称"
            extra="同一账号下名称不能重复。"
            rules={[{ required: true, message: '请填写名称' }]}
          >
            <Input placeholder="例如 demo-app" />
          </Form.Item>
          <Form.Item
            name="archive"
            label="源码包"
            valuePropName="fileList"
            getValueFromEvent={(e) => (Array.isArray(e) ? e : e?.fileList)}
            extra="支持 zip / tar / tar.gz，最大 200MB。登记后可从此项目发起审计。"
            rules={[
              {
                validator: async (_, fileList) => {
                  if (Array.isArray(fileList) && fileList[0]?.originFileObj) return
                  throw new Error('请选择源码包')
                },
              },
            ]}
          >
            <Upload.Dragger maxCount={1} accept={ARCHIVE_ACCEPT} beforeUpload={() => false}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽源码包到这里</p>
            </Upload.Dragger>
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Space>
            <Button type="primary" htmlType="submit" loading={pending}>
              上传登记
            </Button>
            <Button onClick={onClose}>取消</Button>
          </Space>
        </Form>
      )}
    </Drawer>
  )
}
