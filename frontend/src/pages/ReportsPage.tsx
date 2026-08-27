import { useEffect, useMemo, useState } from 'react'
import { Button, Empty, Input, Select, Table, Tabs, Tag, Typography } from 'antd'
import { EyeOutlined, FileProtectOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation, useSearch } from 'wouter'

import { api, type AuditTaskSummary, type ReportSummary } from '../shared/lib/api'
import { getVerdictMeta, getReportStatusMeta, VERDICT_META } from '../shared/lib/meta'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { tableRowNavigateProps } from '../shared/lib/tableRowNavigate'
import { auditResultLabel, projectLabel, reportTypeLabel, sourceVersionLabel } from '../shared/lib/tablePresentation'

const { Text } = Typography

type TabKey = 'audit' | 'verify'

function useReportsTab(): [TabKey, (tab: TabKey) => void] {
  const search = useSearch()
  const [, navigate] = useLocation()
  const tab = useMemo(() => {
    const raw = new URLSearchParams(search).get('tab')
    return raw === 'verify' ? 'verify' : 'audit'
  }, [search])
  const setTab = (next: TabKey) => {
    navigate(next === 'audit' ? '/reports?tab=audit' : '/reports?tab=verify')
  }
  return [tab, setTab]
}

function AuditTasksTable() {
  const [, navigate] = useLocation()
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
    queryKey: ['audit-tasks', { debouncedKeyword, page, pageSize }],
    queryFn: () =>
      api.listAuditTasks({
        limit: String(pageSize),
        offset: String((page - 1) * pageSize),
        ...(debouncedKeyword ? { q: debouncedKeyword } : {}),
      }),
    placeholderData: keepPreviousData,
  })
  useErrorToast(isError, error, '审计任务列表加载失败')

  const columns: ColumnsType<AuditTaskSummary> = [
    {
      title: '项目 / 版本',
      dataIndex: 'project_address',
      ellipsis: true,
      render: (v: string | null, row) => (
        <div>
          <Text strong>{projectLabel(v)}</Text>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {sourceVersionLabel(row.project_ref, null)}
            </Text>
          </div>
        </div>
      ),
    },
    {
      title: '确认 / 可达',
      key: 'counts',
      width: 140,
      render: (_: unknown, row) => (
        <Text>
          {row.confirmed_count} / {row.code_reachable_count}
          {row.vuln_report_count > 0 ? (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                漏洞报告 {row.vuln_report_count}
              </Text>
            </div>
          ) : null}
        </Text>
      ),
    },
    {
      title: '发布状态',
      dataIndex: 'report_status',
      width: 100,
      render: (v: string | null) => {
        if (!v) return <Tag>无任务摘要</Tag>
        const m = getReportStatusMeta(v)
        return <Tag color={m.color}>{m.label}</Tag>
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 140,
      render: (v: string | null) => (v ? dayjs(v).format('MM-DD HH:mm') : '—'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      render: (_: unknown, row) => (
        <Button
          size="small"
          type="primary"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/reports/audits/${row.task_id}`)}
        >
          查看
        </Button>
      ),
    },
  ]

  return (
    <>
      <div className="crucible-filter-bar" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <Input
          allowClear
          placeholder="搜索项目 / 版本 / 任务 ID"
          prefix={<SearchOutlined />}
          style={{ width: 280 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
          刷新
        </Button>
      </div>
      <Table<AuditTaskSummary>
        rowKey="task_id"
        loading={isLoading || isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        locale={{
          emptyText: (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无审计报告">
              <Button type="link" icon={<FileProtectOutlined />} onClick={() => navigate('/tasks')}>
                去代码审计
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
        scroll={{ x: 900 }}
        onRow={(row) => tableRowNavigateProps(() => navigate(`/reports/audits/${row.task_id}`))}
      />
    </>
  )
}

function VerifyReportsTable() {
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
    queryKey: ['verify-reports', { verdictFilter, debouncedKeyword, page, pageSize }],
    queryFn: () =>
      api.listReports({
        task_type: 'verify',
        limit: String(pageSize),
        offset: String((page - 1) * pageSize),
        ...(verdictFilter ? { verdict: verdictFilter } : {}),
        ...(debouncedKeyword ? { q: debouncedKeyword } : {}),
      }),
    placeholderData: keepPreviousData,
  })
  useErrorToast(isError, error, '验证报告列表加载失败')

  const columns: ColumnsType<ReportSummary> = [
    {
      title: '项目 / 版本',
      dataIndex: 'project_address',
      ellipsis: true,
      render: (v: string | null, row) => (
        <div>
          <Text strong>{projectLabel(v)}</Text>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {row.affected_version ?? sourceVersionLabel(row.project_ref, null)} · {row.title}
            </Text>
          </div>
        </div>
      ),
    },
    {
      title: '产品',
      dataIndex: 'product_name',
      width: 120,
      ellipsis: true,
      render: (v: string | null) => v ?? <Text type="secondary">—</Text>,
    },
    {
      title: '报告类型',
      dataIndex: 'document_kind',
      width: 140,
      render: (v: string | null, row) => (
        <Tag color="purple">{reportTypeLabel(v, row.task_type)}</Tag>
      ),
    },
    {
      title: '漏洞结果',
      dataIndex: 'verdict',
      width: 160,
      render: (v: string | null, row) => (
        <div>
          <Tag color={v ? getVerdictMeta(v).color : 'green'}>{auditResultLabel('completed', v)}</Tag>
          {row.severity ? (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                最高风险：{row.severity}
              </Text>
            </div>
          ) : null}
        </div>
      ),
    },
    {
      title: '发布状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => {
        const m = getReportStatusMeta(v)
        return <Tag color={m.color}>{m.label}</Tag>
      },
    },
    {
      title: '生成时间',
      dataIndex: 'created_at',
      width: 140,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      render: (_: unknown, row) => (
        <Button size="small" type="primary" icon={<EyeOutlined />} onClick={() => navigate(`/reports/${row.id}`)}>
          阅读报告
        </Button>
      ),
    },
  ]

  return (
    <>
      <div className="crucible-filter-bar" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
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
        <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
          刷新
        </Button>
      </div>
      <Table<ReportSummary>
        rowKey="id"
        loading={isLoading || isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        locale={{
          emptyText: (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无验证报告">
              <Button type="link" icon={<FileProtectOutlined />} onClick={() => navigate('/tasks')}>
                去代码审计
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
        scroll={{ x: 980 }}
        onRow={(row) => tableRowNavigateProps(() => navigate(`/reports/${row.id}`))}
      />
    </>
  )
}

export function ReportsPage() {
  const [tab, setTab] = useReportsTab()

  return (
    <>
      <PageHeader
        title="漏洞报告"
        subtitle="审计任务下的单漏洞报告，以及验证任务的一次验证一份报告"
      />
      <PageContainer>
        <Tabs
          activeKey={tab}
          onChange={(key) => setTab(key as TabKey)}
          items={[
            { key: 'audit', label: '审计报告', children: <AuditTasksTable /> },
            { key: 'verify', label: '验证报告', children: <VerifyReportsTable /> },
          ]}
        />
      </PageContainer>
    </>
  )
}
