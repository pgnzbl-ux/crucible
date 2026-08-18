import { useEffect, useState } from 'react'
import { Button, Empty, Input, Select, Table, Tag, Typography } from 'antd'
import { FileProtectOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import { api, type ReportSummary } from '../shared/lib/api'
import { getVerdictMeta, getReportStatusMeta, VERDICT_META } from '../shared/lib/meta'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { tableRowNavigateProps } from '../shared/lib/tableRowNavigate'

const { Text } = Typography

export function ReportsPage() {
  const [, navigate] = useLocation()
  const [verdictFilter, setVerdictFilter] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')
  const [debouncedKeyword, setDebouncedKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedKeyword(keyword.trim())
      setPage(1)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [keyword])

  const { data, error, isError, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['reports', { verdictFilter, debouncedKeyword, page, pageSize }],
    queryFn: () =>
      api.listReports({
        limit: String(pageSize),
        offset: String((page - 1) * pageSize),
        ...(verdictFilter ? { verdict: verdictFilter } : {}),
        ...(debouncedKeyword ? { q: debouncedKeyword } : {}),
      }),
    placeholderData: keepPreviousData,
  })
  useErrorToast(isError, error, '报告列表加载失败')

  const columns: ColumnsType<ReportSummary> = [
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
    },
    {
      title: '判定',
      dataIndex: 'verdict',
      width: 120,
      render: (v: string | null) =>
        v ? <Tag color={getVerdictMeta(v).color}>{getVerdictMeta(v).label}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => {
        const m = getReportStatusMeta(v)
        return <Tag color={m.color}>{m.label}</Tag>
      },
    },
    {
      title: '任务',
      dataIndex: 'task_id',
      width: 110,
      render: (v: string) => <Text code>{v.slice(0, 8)}</Text>,
    },
    {
      title: '生成时间',
      dataIndex: 'created_at',
      width: 160,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm:ss'),
    },
  ]

  return (
    <>
      <PageHeader
        title="验证报告"
        subtitle="任务跑完后在这里阅读全文，和本地打开 report.md 一样"
        extra={
          <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
            刷新
          </Button>
        }
      />

      <div className="crucible-filter-bar" style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Select
          allowClear
          placeholder="判定结果"
          style={{ width: 160 }}
          value={verdictFilter}
          onChange={(value) => {
            setVerdictFilter(value)
            setPage(1)
          }}
          options={Object.entries(VERDICT_META).map(([v, m]) => ({ value: v, label: m.label }))}
        />
        <Input
          allowClear
          placeholder="搜索标题 / 任务 ID"
          prefix={<SearchOutlined />}
          style={{ width: 260 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
      </div>

      <PageContainer>
        <Table<ReportSummary>
          rowKey="id"
          loading={isLoading || isFetching}
          columns={columns}
          dataSource={data?.items ?? []}
          locale={{
            emptyText: (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无报告">
                <Button type="link" icon={<FileProtectOutlined />} onClick={() => navigate('/tasks')}>
                  去任务列表
                </Button>
              </Empty>
            ),
          }}
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
          onRow={(row) => tableRowNavigateProps(() => navigate(`/reports/${row.id}`))}
        />
      </PageContainer>
    </>
  )
}
