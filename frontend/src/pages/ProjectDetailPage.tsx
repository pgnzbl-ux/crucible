import { useState } from 'react'
import { Button, Card, Descriptions, Result, Skeleton, Space, Table, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, BugOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation, useRoute } from 'wouter'

import { api, type SourceArtifact } from '../shared/lib/api'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { TaskCreateDrawer } from '../features/task/components/TaskCreateDrawer'

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

  const { data: artifacts, isLoading: artifactsLoading } = useQuery({
    queryKey: ['project-artifacts', projectId],
    queryFn: () => api.listProjectArtifacts(projectId),
    enabled: !!projectId,
  })

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
      title: 'Commit',
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
    <AppLayout>
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
                  label: 'Git 地址',
                  span: 2,
                  children: <Text code>{project.git_url}</Text>,
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
                { key: 'ref', label: '默认引用', children: project.default_ref ?? 'HEAD' },
                {
                  key: 'cloned',
                  label: '最近落地',
                  children: project.last_cloned_at
                    ? dayjs(project.last_cloned_at).format('YYYY-MM-DD HH:mm:ss')
                    : '尚未拉取',
                },
              ]}
            />
            <Card title="已缓存源码包" variant="borderless">
              <Table
                rowKey="id"
                size="small"
                loading={artifactsLoading}
                dataSource={artifacts?.items ?? []}
                columns={columns}
                pagination={false}
                locale={{ emptyText: '还没有缓存。跑过一次源码节点后会出现在这里。' }}
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
        initialValues={
          project
            ? { project_address: project.git_url, project_ref: project.default_ref ?? undefined }
            : undefined
        }
      />
    </AppLayout>
  )
}
