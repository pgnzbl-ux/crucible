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

import { api, type TaskStats, type TaskSummary } from '../shared/lib/api'
import { getStatusMeta, getPriorityMeta } from '../shared/lib/meta'
import { statsPollMs, sumTaskStats } from '../shared/lib/taskListQuery'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { PageHeader } from '../shared/components/PageHeader'
import { StatCard } from '../features/dashboard/components/StatCard'
import { TaskTrendChart, TREND_SAMPLE_NOTE } from '../features/dashboard/components/TaskTrendChart'

const { Text } = Typography

const COUNT_QUERIES = [
  { key: 'queued', title: '排队中', status: 'pending,queued', icon: <ClockCircleOutlined />, tone: 'default' as const, filter: 'pending,queued' },
  { key: 'running', title: '分析中', status: 'running', icon: <ThunderboltOutlined />, tone: 'primary' as const, filter: 'running' },
  { key: 'needsReview', title: '待复核', status: 'needs_review', icon: <BugOutlined />, tone: 'warning' as const, filter: 'needs_review' },
  { key: 'completed', title: '已完成', status: 'completed', icon: <CheckCircleOutlined />, tone: 'success' as const, filter: 'completed' },
  { key: 'failed', title: '失败', status: 'failed', icon: <BugOutlined />, tone: 'error' as const, filter: 'failed' },
  { key: 'total', title: '任务总数', status: undefined, icon: <FileProtectOutlined />, tone: 'default' as const, filter: undefined },
]

function cardValue(stats: TaskStats | undefined, filter?: string): number {
  if (!stats) return 0
  return filter ? sumTaskStats(stats.by_status, filter) : stats.total
}

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
  useErrorToast(isStatsError, statsError, '工作台统计加载失败')
  useErrorToast(isRecentError, recentError, '最近任务加载失败')

  const tasks = data?.items ?? []

  const recentColumns: ColumnsType<TaskSummary> = [
    {
      title: '项目地址',
      dataIndex: 'project_address',
      ellipsis: true,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => {
        const m = getStatusMeta(v)
        return <Tag color={m.color}>{m.label}</Tag>
      },
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 80,
      render: (v: string) => <Tag color={getPriorityMeta(v).color}>{getPriorityMeta(v).label}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 150,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm'),
    },
  ]

  return (
    <>
      <PageHeader
        title="工作台"
        subtitle="AI 漏洞自动验证平台 · 任务总览"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/tasks?create=1')}>
            新建任务
          </Button>
        }
      />

      <Row gutter={[16, 16]} className="crucible-stagger">
        {COUNT_QUERIES.map((card) => (
          <Col xs={24} sm={12} lg={8} key={card.key}>
            <StatCard
              title={card.title}
              value={cardValue(stats, card.status)}
              icon={card.icon}
              tone={card.tone}
              trend={card.key === 'total' ? '全部任务' : '点击筛选'}
              onClick={() =>
                card.filter ? navigate(`/tasks?status=${card.filter}`) : navigate('/tasks')
              }
            />
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={14}>
          <Card className="crucible-card-hover" title={`近 7 日任务趋势（${TREND_SAMPLE_NOTE}）`}>
            {isLoading ? <Skeleton active paragraph={{ rows: 4 }} /> : <TaskTrendChart tasks={tasks} />}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card
            className="crucible-card-hover"
            title="最近任务"
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
                onRow={(row) => ({
                  onClick: () => navigate(`/tasks/${row.id}?tab=progress`),
                  style: { cursor: 'pointer' },
                })}
              />
            ) : (
              <Empty description="暂无任务">
                <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/tasks?create=1')}>
                  新建任务
                </Button>
              </Empty>
            )}
          </Card>
        </Col>
      </Row>
    </>
  )
}
