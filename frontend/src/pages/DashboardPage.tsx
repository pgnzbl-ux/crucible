import { useMemo } from 'react'
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
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import { api, type TaskSummary } from '../shared/lib/api'
import { getStatusMeta, getPriorityMeta } from '../shared/lib/meta'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'

const { Text } = Typography

export function DashboardPage() {
  const [, navigate] = useLocation()
  const { data, isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.listTasks({ limit: '100' }),
    refetchInterval: 5000,
  })

  const stats = useMemo(() => {
    const tasks = data?.items ?? []
    return {
      queued: tasks.filter((t) => ['pending', 'queued'].includes(t.status)).length,
      running: tasks.filter((t) => t.status === 'running').length,
      needsReview: tasks.filter((t) => t.status === 'needs_review').length,
      completed: tasks.filter((t) => t.status === 'completed').length,
      failed: tasks.filter((t) => t.status === 'failed').length,
      total: tasks.length,
    }
  }, [data])

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
    { title: '排队中', value: stats.queued, icon: <ClockCircleOutlined />, tone: 'default' },
    { title: '分析中', value: stats.running, icon: <ThunderboltOutlined />, tone: 'primary' },
    { title: '待复核', value: stats.needsReview, icon: <BugOutlined />, tone: 'warning' },
    { title: '已完成', value: stats.completed, icon: <CheckCircleOutlined />, tone: 'success' },
    { title: '失败', value: stats.failed, icon: <BugOutlined />, tone: 'error' },
    { title: '任务总数', value: stats.total, icon: <FileProtectOutlined />, tone: 'default' },
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
            <Card className="stat-card">
              <div className={`stat-card-icon stat-card-icon-${card.tone}`}>{card.icon}</div>
              <div className="stat-card-title">{card.title}</div>
              <div className="stat-card-value">{card.value}</div>
            </Card>
          </Col>
        ))}
      </Row>

      <Card
        style={{ marginTop: 24 }}
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
        ) : (data?.items ?? []).length ? (
          <Table
            rowKey="id"
            size="middle"
            columns={recentColumns}
            dataSource={(data?.items ?? []).slice(0, 8)}
            pagination={false}
            onRow={() => ({
              onClick: () => navigate('/tasks'),
              style: { cursor: 'pointer' },
            })}
          />
        ) : (
          <Empty description="暂无任务" />
        )}
      </Card>
    </AppLayout>
  )
}
