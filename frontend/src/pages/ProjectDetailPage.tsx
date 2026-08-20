import { useState } from 'react'
import { Button, Card, Descriptions, Result, Skeleton, Space, Table, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, BugOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation, useRoute } from 'wouter'

import { api, type SourceArtifact } from '../shared/lib/api'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { TaskCreateDrawer } from '../features/task/components/TaskCreateDrawer'
import { classifyProjectRef, projectDefaultRefLabel } from '../features/task/lib/projectSelectOptions'
import { useErrorToast } from '../shared/hooks/useErrorToast'

const { Text } = Typography

export function ProjectDetailPage() {
  const [, params] = useRoute('/projects/:id')
  const [, navigate] = useLocation()
  const projectId = params?.id ?? ''
  const [createOpen, setCreateOpen] = useState(false)

  const { data: project, isLoading, isError } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId),
    enabled: !!projectId,
  })

  const { data: artifacts, isLoading: artifactsLoading, isError: isArtifactsError, error: artifactsError } = useQuery({
    queryKey: ['project-artifacts', projectId],
    queryFn: () => api.listProjectArtifacts(projectId),
    enabled: !!projectId,
  })
  useErrorToast(isArtifactsError, artifactsError, '制品列表加载失败')

  const columns: ColumnsType<SourceArtifact> = [
    {
      title: '引用',
      render: (_, row) => (
        <span>
          {row.ref_type} / {row.ref_name}
        </span>
      ),
    },
    {
              title: 'Commit / 指纹',
      dataIndex: 'commit_sha',
      width: 120,
      render: (v: string) => <Text code>{v.slice(0, 7)}</Text>,
    },
    {
      title: '落地目录',
      dataIndex: 'repo_dirname',
      width: 140,
    },
    {
      title: 'MinIO',
      dataIndex: 'object_url',
      ellipsis: true,
      render: (v: string) => <Text type="secondary">{v}</Text>,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 170,
      render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm'),
    },
  ]

  return (
    <>
      <PageHeader
        title={project?.name ?? '源码项目'}
        subtitle={project?.git_url}
        extra={
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/projects')}>
              返回列表
            </Button>
            {project && (
              <Button
                type="primary"
                icon={<BugOutlined />}
                disabled={project.is_web === false}
                title={project.is_web === false ? '非 Web 项目不能开漏洞验证' : undefined}
                onClick={() => setCreateOpen(true)}
              >
                新建验证任务
              </Button>
            )}
          </Space>
        }
      />
      <PageContainer>
        {isLoading && !project ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : project ? (
          <>
            <Descriptions
              column={2}
              size="small"
              bordered
              style={{ marginBottom: 16 }}
              items={[
                {
                  key: 'git',
                  label: project.source_type === 'local_upload' ? '源码标识' : 'Git 地址',
                  span: 2,
                  children: <Text code>{project.git_url}</Text>,
                },
                {
                  key: 'source',
                  label: '来源',
                  children:
                    project.source_type === 'local_upload' ? (
                      <Tag>本地上传</Tag>
                    ) : (
                      <Tag>Git</Tag>
                    ),
                },
                {
                  key: 'web',
                  label: '是否 Web',
                  children:
                    project.is_web == null ? (
                      <Text type="secondary">尚未画像</Text>
                    ) : (
                      <Tag color={project.is_web ? 'green' : 'default'}>{project.is_web ? 'Web' : '非 Web'}</Tag>
                    ),
                },
                {
                  key: 'stack',
                  label: '语言 / 框架',
                  children:
                    [project.detected_language, project.detected_framework].filter(Boolean).join(' / ') || '—',
                },
                ...(project.source_type === 'local_upload'
                  ? []
                  : [
                      {
                        key: 'ref',
                        label: '默认引用',
                        children: projectDefaultRefLabel(project),
                      },
                    ]),
                {
                  key: 'cloned',
                  label: '最近落地',
                  children: project.last_cloned_at
                    ? dayjs(project.last_cloned_at).format('YYYY-MM-DD HH:mm:ss')
                    : '尚未拉取',
                },
              ]}
            />
            <Card
              title={project.source_type === 'local_upload' ? '原始源码包' : '已缓存源码包'}
              variant="borderless"
            >
              <Table
                rowKey="id"
                size="small"
                loading={artifactsLoading}
                dataSource={artifacts?.items ?? []}
                columns={columns}
                pagination={false}
                locale={{
                  emptyText: isArtifactsError
                    ? '制品列表加载失败'
                    : project.source_type === 'local_upload'
                      ? '尚未入库原始包。请从源码管理重新上传。'
                      : '还没有缓存。跑过一次源码节点后会出现在这里。',
                }}
              />
            </Card>
          </>
        ) : (
          <Result
            status={isError ? 'error' : '404'}
            title={isError ? '加载失败' : '项目不存在'}
            extra={
              <Button type="primary" onClick={() => navigate('/projects')}>
                返回列表
              </Button>
            }
          />
        )}
      </PageContainer>
      <TaskCreateDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        boundProject={
          project
            ? {
                id: project.id,
                name: project.name,
                git_url: project.git_url,
                default_ref: project.default_ref,
                default_ref_type: project.default_ref_type ?? undefined,
                source_refs: project.source_refs,
                source_type: project.source_type,
                artifacts: artifacts?.items,
              }
            : undefined
        }
        initialValues={
          project
            ? {
                project_ref: project.default_ref ?? undefined,
                project_ref_type: (project.default_ref_type ??
                  classifyProjectRef(project.default_ref).ref_type) as
                  | 'branch'
                  | 'tag'
                  | 'commit',
                clone_depth: 1,
              }
            : undefined
        }
      />
    </>
  )
}
