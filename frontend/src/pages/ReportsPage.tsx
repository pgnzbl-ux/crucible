import { Button, Card, Table, Tag, Typography } from 'antd'
import { FileProtectOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { api, type ReportDetail } from '../shared/lib/api'
import { getConclusionMeta } from '../shared/lib/meta'
import { AppLayout } from '../app/layout'

const { Title, Text } = Typography

export function ReportsPage() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['reports'],
    queryFn: () =>
      api.listTasks({ limit: '200' }).then(async (tasks) => {
        // 逐个任务查报告（分页小场景直接聚合）
        const reports: Array<ReportDetail & { project_address: string }> = []
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

  const columns: ColumnsType<ReportDetail & { project_address: string }> = [
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
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
    },
    {
      title: '结论',
      dataIndex: 'conclusion',
      width: 140,
      render: (v: string) => {
        const m = getConclusionMeta(v)
        return <Tag color={m.color}>{m.label}</Tag>
      },
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
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Title level={4} style={{ marginBottom: 4 }}>
            验证报告
          </Title>
          <Text type="secondary">Agent 分析产出的结构化验证报告</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
          刷新
        </Button>
      </div>
      <Card>
        <Table
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={data ?? []}
          pagination={{ pageSize: 10 }}
          locale={{ emptyText: '暂无报告' }}
        />
      </Card>
    </AppLayout>
  )
}
