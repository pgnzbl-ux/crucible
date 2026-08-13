import { Button, Modal, Space, Table, Tag, Typography } from 'antd'
import {
  DeleteOutlined,
  PauseCircleOutlined,
  RedoOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import type { TaskSummary } from '../../../shared/lib/api'
import { getStatusMeta, getPriorityMeta, getVerdictMeta } from '../../../shared/lib/meta'

const { Text } = Typography

interface TaskTableProps {
  data: TaskSummary[]
  loading: boolean
  total: number
  onCancel: (id: string) => void
  onRetry: (id: string) => void
  onDelete: (id: string) => void
  cancelPending?: boolean
  retryPending?: boolean
  deletePending?: boolean
}

export function TaskTable({
  data,
  loading,
  total,
  onCancel,
  onRetry,
  onDelete,
  cancelPending,
  retryPending,
  deletePending,
}: TaskTableProps) {
  const [, navigate] = useLocation()

  const columns: ColumnsType<TaskSummary> = [
    {
      title: '项目地址',
      dataIndex: 'project_address',
      ellipsis: true,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (v: string) => {
        const m = getStatusMeta(v)
        return <Tag color={m.color}>{m.label}</Tag>
      },
    },
    {
      title: '判定',
      dataIndex: 'verdict',
      width: 110,
      render: (v: string | null) =>
        v ? <Tag color={getVerdictMeta(v).color}>{getVerdictMeta(v).label}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 90,
      render: (v: string) => <Tag color={getPriorityMeta(v).color}>{getPriorityMeta(v).label}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      fixed: 'right',
      render: (_, row) => (
        <Space size="small" wrap onClick={(e) => e.stopPropagation()}>
          <Button size="small" type="link" onClick={() => navigate(`/tasks/${row.id}`)}>
            详情
          </Button>
          {['queued', 'running', 'pending'].includes(row.status) && (
            <Button
              size="small"
              danger
              icon={<PauseCircleOutlined />}
              onClick={() => onCancel(row.id)}
              loading={cancelPending}
            >
              取消
            </Button>
          )}
          {['failed', 'cancelled', 'completed', 'needs_review'].includes(row.status) && (
            <Button
              size="small"
              icon={<RedoOutlined />}
              onClick={() => onRetry(row.id)}
              loading={retryPending}
            >
              重试
            </Button>
          )}
          {!['running', 'pending', 'queued'].includes(row.status) && (
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => {
                Modal.confirm({
                  title: '删除任务',
                  content: '任务及其运行记录将被归档(软删)。确定继续?',
                  okText: '删除',
                  okType: 'danger',
                  cancelText: '取消',
                  onOk: () => onDelete(row.id),
                })
              }}
              loading={deletePending}
            />
          )}
        </Space>
      ),
    },
  ]

  return (
    <Table
      rowKey="id"
      loading={loading}
      columns={columns}
      dataSource={data}
      scroll={{ x: 900 }}
      pagination={{ pageSize: 10, total, showTotal: (t) => `共 ${t} 条` }}
      onRow={(row) => ({
        onClick: () => navigate(`/tasks/${row.id}`),
        style: { cursor: 'pointer' },
      })}
    />
  )
}
