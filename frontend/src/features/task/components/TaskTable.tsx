import { App, Button, Empty, Space, Table, Tag, Typography } from 'antd'
import {
  DeleteOutlined,
  PauseCircleOutlined,
  PlusOutlined,
  RedoOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import type { TaskSummary } from '../../../shared/lib/api'
import { getStatusMeta, getPriorityMeta, getVerdictMeta } from '../../../shared/lib/meta'
import { canCancel, canDelete, canRetry, CONFIRM_COPY } from '../../../shared/lib/taskActions'

const { Text } = Typography

interface TaskTableProps {
  data: TaskSummary[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number, pageSize: number) => void
  onCancel: (id: string) => void
  onRetry: (id: string) => void
  onDelete: (id: string) => void
  onCreate: () => void
  pendingId?: string | null
}

export function TaskTable({
  data,
  loading,
  total,
  page,
  pageSize,
  onPageChange,
  onCancel,
  onRetry,
  onDelete,
  onCreate,
  pendingId,
}: TaskTableProps) {
  const [, navigate] = useLocation()
  const { modal } = App.useApp()

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
          <Button size="small" type="link" onClick={() => navigate(`/tasks/${row.id}?tab=progress`)}>
            详情
          </Button>
          {canCancel(row.status) && (
            <Button
              size="small"
              danger
              icon={<PauseCircleOutlined />}
              onClick={() => {
                modal.confirm({
                  title: CONFIRM_COPY.cancel.title,
                  content: CONFIRM_COPY.cancel.content,
                  okText: CONFIRM_COPY.cancel.okText,
                  okType: 'danger',
                  cancelText: '返回',
                  onOk: () => onCancel(row.id),
                })
              }}
              loading={pendingId === row.id}
            >
              取消
            </Button>
          )}
          {canRetry(row.status) && (
            <Button
              size="small"
              icon={<RedoOutlined />}
              onClick={() => {
                modal.confirm({
                  title: CONFIRM_COPY.retry.title,
                  content: CONFIRM_COPY.retry.content,
                  okText: CONFIRM_COPY.retry.okText,
                  cancelText: '返回',
                  onOk: () => onRetry(row.id),
                })
              }}
              loading={pendingId === row.id}
            >
              重试
            </Button>
          )}
          {canDelete(row.status) && (
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={(e) => {
                e.stopPropagation()
                modal.confirm({
                  title: CONFIRM_COPY.delete.title,
                  content: CONFIRM_COPY.delete.content,
                  okText: CONFIRM_COPY.delete.okText,
                  okType: 'danger',
                  cancelText: '返回',
                  onOk: () => onDelete(row.id),
                })
              }}
              loading={pendingId === row.id}
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
      locale={{
        emptyText: (
          <Empty description="暂无任务">
            <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
              新建任务
            </Button>
          </Empty>
        ),
      }}
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        pageSizeOptions: [10, 20, 50],
        showTotal: (t) => `共 ${t} 条`,
        onChange: onPageChange,
      }}
      onRow={(row) => ({
        onClick: () => navigate(`/tasks/${row.id}?tab=progress`),
        style: { cursor: 'pointer' },
      })}
    />
  )
}
