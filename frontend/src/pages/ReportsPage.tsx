import { useMemo, useState } from 'react'
import { Button, Card, Empty, Input, Select, Skeleton, Table, Tag, Typography } from 'antd'
import { FileProtectOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { api, type TaskSummary } from '../shared/lib/api'
import { getVerdictMeta, VERDICT_META } from '../shared/lib/meta'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { ReportContent } from '../shared/components/ReportContent'

const { Text } = Typography

type ReportRow = {
  task_id: string
  project_address: string
  verdict: string | null
  cvss_score: number | null
  severity: string | null
  status: string | null
  created_at: string | null
  hasReport: boolean
}

function ReportExpandedRow({ taskId }: { taskId: string }) {
  const { data: report, isLoading } = useQuery({
    queryKey: ['task-report', taskId],
    queryFn: () => api.getReportByTask(taskId),
    retry: false,
  })

  if (isLoading) return <Skeleton active paragraph={{ rows: 4 }} />
  if (!report) return <Empty description="暂无报告详情" />
  return <ReportContent report={report} />
}

export function ReportsPage() {
  const [verdictFilter, setVerdictFilter] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')

  const { data: tasksData, isLoading, refetch } = useQuery({
    queryKey: ['reports-tasks'],
    queryFn: () => api.listTasks({ limit: '200' }),
    refetchInterval: 8000,
  })

  const rows: ReportRow[] = useMemo(() => {
    return (tasksData?.items ?? [])
      .filter((t: TaskSummary) => t.verdict != null || t.status === 'completed' || t.status === 'needs_review')
      .map((t: TaskSummary) => ({
        task_id: t.id,
        project_address: t.project_address,
        verdict: t.verdict,
        cvss_score: null,
        severity: null,
        status: t.status,
        created_at: t.created_at,
        hasReport: true,
      }))
  }, [tasksData])

  const filtered = useMemo(() => {
    let result = rows
    if (verdictFilter) {
      result = result.filter((r) => r.verdict === verdictFilter)
    }
    if (keyword) {
      const q = keyword.toLowerCase()
      result = result.filter(
        (r) =>
          r.project_address.toLowerCase().includes(q) ||
          r.task_id.toLowerCase().includes(q),
      )
    }
    return result
  }, [rows, verdictFilter, keyword])

  const columns: ColumnsType<ReportRow> = [
    {
      title: '任务',
      dataIndex: 'task_id',
      width: 110,
      render: (v: string) => <Text code>{v.slice(0, 8)}</Text>,
    },
    {
      title: '项目地址',
      dataIndex: 'project_address',
      ellipsis: true,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '判定',
      dataIndex: 'verdict',
      width: 120,
      render: (v: string | null) =>
        v ? <Tag color={getVerdictMeta(v).color}>{getVerdictMeta(v).label}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '任务状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string | null) => (v ? <Tag>{v}</Tag> : <Text type="secondary">—</Text>),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: (v: string | null) => (v ? dayjs(v).format('MM-DD HH:mm:ss') : '—'),
    },
  ]

  return (
    <AppLayout>
      <PageHeader
        title="验证报告"
        subtitle="Agent 分析产出的结构化验证报告（点击行展开详情）"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
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
          onChange={setVerdictFilter}
          options={Object.entries(VERDICT_META).map(([v, m]) => ({ value: v, label: m.label }))}
        />
        <Input
          allowClear
          placeholder="搜索项目地址 / 任务 ID"
          prefix={<SearchOutlined />}
          style={{ width: 260 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
      </div>

      <PageContainer>
        {isLoading ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : filtered.length ? (
          <Table<ReportRow>
            rowKey="task_id"
            columns={columns}
            dataSource={filtered}
            pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
            expandable={{
              expandedRowRender: (row) => <ReportExpandedRow taskId={row.task_id} />,
              rowExpandable: () => true,
            }}
          />
        ) : (
          <Empty description="暂无报告" image={<FileProtectOutlined style={{ fontSize: 40 }} />} />
        )}
      </PageContainer>
    </AppLayout>
  )
}
