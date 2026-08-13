import { useMemo } from 'react'
import { App, Button, Card, Col, Empty, Row, Skeleton, Table, Tag, Typography } from 'antd'
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
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import { api, type TaskSummary } from '../shared/lib/api'
import { getStatusMeta, getPriorityMeta } from '../shared/lib/meta'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { StatCard } from '../features/dashboard/components/StatCard'
import { TaskTrendChart } from '../features/dashboard/components/TaskTrendChart'

const { Text } = Typography

export function DashboardPage() {
  const [, navigate] = useLocation()
  const { data, isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.listTasks({ limit: '100' }),
    refetchInterval: 5000,
  })

  const tasks = data?.items ?? []

  const stats = useMemo(() => {
    return {
      queued: tasks.filter((t) => ['pending', 'queued'].includes(t.status)).length,
      running: tasks.filter((t) => t.status === 'running').length,
      needsReview: tasks.filter((t) => t.status === 'needs_review').length,
      completed: tasks.filter((t) => t.status === 'completed').length,
      failed: tasks.filter((t) => t.status === 'failed').length,
      total: tasks.length,
    }
  }, [tasks])

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

  const statCards = [
    {
      title: '排队中',
      value: stats.queued,
      icon: <ClockCircleOutlined />,
      tone: 'default' as const,
      filter: 'pending,queued',
    },
    {
      title: '分析中',
      value: stats.running,
      icon: <ThunderboltOutlined />,
      tone: 'primary' as const,
      filter: 'running',
    },
    {
      title: '待复核',
      value: stats.needsReview,
      icon: <BugOutlined />,
      tone: 'warning' as const,
      filter: 'needs_review',
    },
    {
      title: '已完成',
      value: stats.completed,
      icon: <CheckCircleOutlined />,
      tone: 'success' as const,
      filter: 'completed',
    },
    {
      title: '失败',
      value: stats.failed,
      icon: <BugOutlined />,
      tone: 'error' as const,
      filter: 'failed',
    },
    {
      title: '任务总数',
      value: stats.total,
      icon: <FileProtectOutlined />,
      tone: 'default' as const,
      filter: undefined,
    },
  ]

  return (
    <AppLayout>
      <PageHeader
        title="工作台"
        subtitle="AI 漏洞自动验证平台 · 任务总览"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/tasks')}>
            新建任务
          </Button>
        }
      />

      <Row gutter={[16, 16]} className="crucible-stagger">
        {statCards.map((card) => (
          <Col xs={24} sm={12} lg={8} key={card.title}>
            <StatCard
              title={card.title}
              value={card.value}
              icon={card.icon}
              tone={card.tone}
              trend="近 7 日"
              onClick={() =>
                card.filter ? navigate(`/tasks?status=${card.filter}`) : navigate('/tasks')
              }
            />
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={14}>
          <Card className="crucible-card-hover" title="近 7 日任务趋势">
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
                  onClick: () => navigate(`/tasks/${row.id}`),
                  style: { cursor: 'pointer' },
                })}
              />
            ) : (
              <Empty description="暂无任务" />
            )}
          </Card>
        </Col>
      </Row>
    </AppLayout>
  )
}
