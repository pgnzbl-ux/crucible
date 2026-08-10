import { useMemo } from 'react'
import { Button, Card, Col, Row, Statistic, Table, Tag, Typography } from 'antd'
import {
  BugOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
  FileProtectOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import { api, type TaskSummary } from '../shared/lib/api'
import { getStatusMeta, getPriorityMeta } from '../shared/lib/meta'
import { AppLayout } from '../app/layout'

const { Title, Text } = Typography

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

  return (
    <AppLayout>
      <Title level={4} style={{ marginBottom: 4 }}>
        Crucible 工作台
      </Title>
      <Text type="secondary">AI 漏洞自动验证平台 · 任务总览</Text>

      <Row gutter={[16, 16]} style={{ marginTop: 20 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="排队中" value={stats.queued} prefix={<ClockCircleOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="分析中" value={stats.running} prefix={<ThunderboltOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="待复核"
              value={stats.needsReview}
              prefix={<BugOutlined />}
              valueStyle={{ color: '#d46b08' }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="已完成"
              value={stats.completed}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#3f8600' }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="失败" value={stats.failed} prefix={<BugOutlined />} valueStyle={{ color: '#cf1322' }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="任务总数" value={stats.total} prefix={<FileProtectOutlined />} />
          </Card>
        </Col>
      </Row>

      <Card
        style={{ marginTop: 24 }}
        title="最近任务"
        extra={
          <Button type="link" onClick={() => navigate('/tasks')}>
            查看全部 <ArrowRightOutlined />
          </Button>
        }
      >
        <Table
          rowKey="id"
          size="small"
          loading={isLoading}
          columns={recentColumns}
          dataSource={(data?.items ?? []).slice(0, 8)}
          pagination={false}
        />
      </Card>
    </AppLayout>
  )
}
