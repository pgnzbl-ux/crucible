import { Button, Card, Empty, Skeleton, Table, Tag, Typography } from 'antd'
import { FileProtectOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { api, type ReportDetail } from '../shared/lib/api'
import { getVerdictMeta } from '../shared/lib/meta'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'
import { ReportContent } from '../shared/components/ReportContent'

const { Text } = Typography

type ReportRow = ReportDetail & { project_address: string }

export function ReportsPage() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['reports'],
    queryFn: () =>
      api.listTasks({ limit: '200' }).then(async (tasks) => {
        const reports: ReportRow[] = []
        for (const t of tasks.items) {
          try {
            const r = await api.getReportByTask(t.id)
            reports.push({ ...r, project_address: t.project_address })
          } catch {
            // 无报告的任务跳过
          }
        }
        return reports
      }),
    refetchInterval: 8000,
  })

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
      title: 'CVSS',
      dataIndex: 'cvss_score',
      width: 90,
      render: (v: number | null) => (v != null ? <Text strong>{v.toFixed(1)}</Text> : <Text type="secondary">—</Text>),
    },
    {
      title: '严重度',
      dataIndex: 'severity',
      width: 90,
      render: (v: string | null) => (v ? <Tag>{v}</Tag> : <Text type="secondary">—</Text>),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => <Tag color={v === 'published' ? 'green' : 'blue'}>{v}</Tag>,
    },
    {
      title: '生成时间',
      dataIndex: 'created_at',
      width: 160,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm:ss'),
    },
  ]

  return (
    <AppLayout>
      <PageHeader
        title="验证报告"
        subtitle="Agent 分析产出的结构化验证报告(点击行展开 8 节详情)"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
            刷新
          </Button>
        }
      />
      <Card className="crucible-card-hover">
        {isLoading ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : (data ?? []).length ? (
          <Table<ReportRow>
            rowKey="id"
            columns={columns}
            dataSource={data ?? []}
            pagination={{ pageSize: 10 }}
            expandable={{
              expandedRowRender: (row) => <ReportContent report={row} />,
              rowExpandable: () => true,
            }}
          />
        ) : (
          <Empty description="暂无报告" image={<FileProtectOutlined style={{ fontSize: 40 }} />} />
        )}
      </Card>
    </AppLayout>
  )
}
