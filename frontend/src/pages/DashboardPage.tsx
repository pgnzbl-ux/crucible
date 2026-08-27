import { useQuery } from '@tanstack/react-query'
import { Button, Card, Col, Empty, Row, Skeleton, Table, Tag, Typography } from 'antd'
import {
  BugOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
  FileProtectOutlined,
  PlusOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import { api, type TaskSummary } from '../shared/lib/api'
import { getStatusMeta } from '../shared/lib/meta'
import { auditResultLabel, projectLabel, sourceVersionLabel } from '../shared/lib/tablePresentation'
import { statsPollMs, sumTaskStats } from '../shared/lib/taskListQuery'
import { tableRowNavigateProps } from '../shared/lib/tableRowNavigate'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { PageHeader } from '../shared/components/PageHeader'
import { StatCard } from '../features/dashboard/components/StatCard'
import { TaskTrendChart, TREND_SAMPLE_NOTE } from '../features/dashboard/components/TaskTrendChart'

const { Text } = Typography

export function DashboardPage() {
  const [, navigate] = useLocation()

  const {
    data: stats,
    error: statsError,
    isError: isStatsError,
  } = useQuery({
    queryKey: ['task-stats'],
    queryFn: () => api.getTaskStats(),
    refetchInterval: (query) => statsPollMs(query.state.data?.by_status),
  })

  const { data, error: recentError, isError: isRecentError, isLoading } = useQuery({
    queryKey: ['tasks', 'dashboard-recent'],
    queryFn: () => api.listTasks({ limit: '200' }),
    refetchInterval: () => statsPollMs(stats?.by_status),
  })
  const { data: findingStats, error: findingStatsError, isError: isFindingStatsError } = useQuery({
    queryKey: ['finding-stats'],
    queryFn: () => api.getFindingStats(),
    refetchInterval: () => statsPollMs(stats?.by_status),
  })
  useErrorToast(isStatsError, statsError, '工作台统计加载失败')
  useErrorToast(isRecentError, recentError, '最近任务加载失败')
  useErrorToast(isFindingStatsError, findingStatsError, '漏洞线索统计加载失败')

  const tasks = data?.items ?? []
  const cards = [
    { key: 'running', title: '进行中的审计', value: sumTaskStats(stats?.by_status ?? {}, 'pending,queued,running'), icon: <ThunderboltOutlined />, tone: 'primary' as const, href: '/tasks?status=pending%2Cqueued%2Crunning', trend: '实时分析中' },
    { key: 'review', title: '验证中', value: findingStats?.by_queue.verifying ?? 0, icon: <ClockCircleOutlined />, tone: 'warning' as const, href: '/findings?scope=verifying', trend: 'AI 定向复核' },
    { key: 'confirming', title: '已确认漏洞', value: findingStats?.by_queue.confirmed ?? 0, icon: <CheckCircleOutlined />, tone: 'success' as const, href: '/findings?scope=confirmed', trend: '完全证实真漏洞' },
    { key: 'confirmed', title: '代码可达', value: findingStats?.by_queue.reachable ?? 0, icon: <BugOutlined />, tone: 'warning' as const, href: '/findings?scope=reachable', trend: '调用链路闭环' },
    { key: 'findings', title: '工作台线索', value: findingStats?.by_queue.workbench ?? 0, icon: <BugOutlined />, tone: 'default' as const, href: '/findings', trend: '待研判线索池' },
    { key: 'total', title: '全部审计运行', value: stats?.total ?? 0, icon: <FileProtectOutlined />, tone: 'default' as const, href: '/tasks', trend: '历史任务归档' },
  ]

  const recentColumns: ColumnsType<TaskSummary> = [
    {
      title: '项目 / 版本',
      dataIndex: 'project_address',
      ellipsis: true,
      render: (v: string, row) => (
        <div>
          <Text strong>{projectLabel(v)}</Text>
          <div><Text type="secondary" style={{ fontSize: 12 }}>{sourceVersionLabel(row.project_ref, row.project_ref_type)}</Text></div>
        </div>
      ),
    },
    {
      title: '当前状态',
      dataIndex: 'status',
      width: 110,
      render: (v: string) => {
        const m = getStatusMeta(v)
        return <Tag color={m.color}>{m.label}</Tag>
      },
    },
    {
      title: '审计结果',
      dataIndex: 'verdict',
      width: 160,
      render: (v: string | null, row) => (
        <div>
          <Text>{auditResultLabel(row.status, v)}</Text>
          {row.task_type === 'discovery' ? (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                线索 {row.finding_count} · 确认 <span style={{ color: row.confirmed_count > 0 ? '#52c41a' : undefined, fontWeight: row.confirmed_count > 0 ? 600 : undefined }}>{row.confirmed_count}</span>
              </Text>
            </div>
          ) : null}
        </div>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 140,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm'),
    },
  ]

  return (
    <>
      <PageHeader
        title="工作台"
        subtitle="代码审计、漏洞线索与终认状态总览"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/tasks?create=1')}>
            发起代码审计
          </Button>
        }
      />

      <Row gutter={[16, 16]} className="crucible-stagger">
        {cards.map((card) => (
          <Col xs={24} sm={12} lg={8} key={card.key}>
            <StatCard
              title={card.title}
              value={card.value}
              icon={card.icon}
              tone={card.tone}
              trend={card.trend}
              onClick={() => navigate(card.href)}
            />
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={14}>
          <Card className="crucible-card-hover" title={`近 7 日审计趋势（${TREND_SAMPLE_NOTE}）`}>
            {isLoading ? <Skeleton active paragraph={{ rows: 4 }} /> : <TaskTrendChart tasks={tasks} />}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card
            className="crucible-card-hover"
            title="最近审计"
            extra={
              <Button type="link" onClick={() => navigate('/tasks')}>
                查看全部 <ArrowRightOutlined />
              </Button>
            }
          >
            {isLoading ? (
              <Skeleton active paragraph={{ rows: 5 }} />
            ) : tasks.length ? (
              <Table
                rowKey="id"
                size="middle"
                columns={recentColumns}
                dataSource={tasks.slice(0, 8)}
                pagination={false}
                onRow={(row) =>
                  tableRowNavigateProps(() => navigate(`/tasks/${row.id}?tab=progress`))
                }
              />
            ) : (
              <Empty description="暂无审计运行">
                <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/tasks?create=1')}>
                  发起代码审计
                </Button>
              </Empty>
            )}
          </Card>
        </Col>
      </Row>
    </>
  )
}
