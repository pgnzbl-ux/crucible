import { useState } from 'react'
import { Alert, Button, Empty, Space, Table, Tag, Typography } from 'antd'
import { CodeOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import { api, type Project } from '../shared/lib/api'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'

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

  const { data, error, isError, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['projects', { page, pageSize }],
    queryFn: () =>
      api.listProjects({ limit: String(pageSize), offset: String((page - 1) * pageSize) }),
    placeholderData: keepPreviousData,
  })

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
    <AppLayout>
      <PageHeader
        title="源码管理"
        subtitle="同一 Git 仓库只登记一次，后续任务优先从 MinIO 缓存取源码"
        extra={
          <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
            刷新
          </Button>
        }
      />
      <PageContainer>
        {isError && (
          <Alert
            type="error"
            showIcon
            title="项目列表加载失败"
            description={error.message}
            style={{ marginBottom: 16 }}
          />
        )}
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
          onRow={(row) => ({
            onClick: () => navigate(`/projects/${row.id}`),
            style: { cursor: 'pointer' },
          })}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="还没有项目。提交任务时会按 Git 地址自动登记。"
              >
                <Button type="primary" icon={<CodeOutlined />} onClick={() => navigate('/tasks?create=1')}>
                  去新建任务
                </Button>
              </Empty>
            ),
          }}
        />
      </PageContainer>
    </AppLayout>
  )
}
