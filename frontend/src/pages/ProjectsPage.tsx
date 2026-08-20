import { useState } from 'react'
import { Button, Empty, Space, Table, Tag, Typography } from 'antd'
import { CloudUploadOutlined, GithubOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import { api, type Project } from '../shared/lib/api'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { tableRowNavigateProps } from '../shared/lib/tableRowNavigate'
import { RegisterSourceDrawer } from '../features/project/RegisterSourceDrawer'

const { Text } = Typography

function webTag(isWeb: boolean | null) {
  if (isWeb === true) return <Tag color="green">Web</Tag>
  if (isWeb === false) return <Tag>非 Web</Tag>
  return <Text type="secondary">未画像</Text>
}

export function ProjectsPage() {
  const [, navigate] = useLocation()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [registerMode, setRegisterMode] = useState<'git' | 'upload' | null>(null)

  const { data, error, isError, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['projects', { page, pageSize }],
    queryFn: () =>
      api.listProjects({ limit: String(pageSize), offset: String((page - 1) * pageSize) }),
    placeholderData: keepPreviousData,
  })
  useErrorToast(isError, error, '项目列表加载失败')

  const columns: ColumnsType<Project> = [
    {
      title: '项目',
      dataIndex: 'name',
      render: (name: string, row) => (
        <div>
          <div>{name}</div>
          <Text type="secondary" style={{ fontSize: 12 }} code>
            {row.git_url}
          </Text>
          {row.source_type === 'local_upload' ? (
            <div>
              <Tag>本地上传</Tag>
            </div>
          ) : null}
        </div>
      ),
    },
    {
      title: '画像',
      width: 220,
      render: (_, row) => {
        const stack = [row.detected_language, row.detected_framework].filter(Boolean).join(' / ')
        return (
          <Space size={4} wrap>
            {webTag(row.is_web)}
            {stack ? <Text type="secondary">{stack}</Text> : null}
          </Space>
        )
      },
    },
    {
      title: '默认引用',
      dataIndex: 'default_ref',
      width: 120,
      render: (v: string | null) => v || <Text type="secondary">HEAD</Text>,
    },
    {
      title: '最近落地',
      dataIndex: 'last_cloned_at',
      width: 170,
      render: (v: string | null) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '—'),
    },
  ]

  return (
    <>
      <PageHeader
        title="源码管理"
        subtitle="登记 Git 仓库或上传源码包；Git 按 commit 缓存，上传包按名称唯一保存原始文件"
        extra={
          <Space>
            <Button icon={<GithubOutlined />} onClick={() => setRegisterMode('git')}>
              登记 Git
            </Button>
            <Button icon={<CloudUploadOutlined />} onClick={() => setRegisterMode('upload')}>
              上传源码包
            </Button>
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
          </Space>
        }
      />
      <PageContainer>
        <Table
          rowKey="id"
          loading={isLoading || isFetching}
          dataSource={data?.items ?? []}
          columns={columns}
          pagination={{
            current: page,
            pageSize,
            total: data?.total ?? 0,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50],
            showTotal: (total) => `共 ${total} 条`,
            onChange: (nextPage, nextPageSize) => {
              setPage(nextPageSize === pageSize ? nextPage : 1)
              setPageSize(nextPageSize)
            },
          }}
          onRow={(row) => tableRowNavigateProps(() => navigate(`/projects/${row.id}`))}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="还没有项目。先登记 Git 仓库或上传源码包，再从项目详情开验证任务。"
              >
                <Space>
                  <Button icon={<GithubOutlined />} onClick={() => setRegisterMode('git')}>
                    登记 Git
                  </Button>
                  <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setRegisterMode('upload')}>
                    上传源码包
                  </Button>
                </Space>
              </Empty>
            ),
          }}
        />
      </PageContainer>
      <RegisterSourceDrawer
        open={registerMode !== null}
        mode={registerMode ?? 'git'}
        onClose={() => setRegisterMode(null)}
      />
    </>
  )
}
