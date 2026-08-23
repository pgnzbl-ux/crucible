import { App, Button, Dropdown, Empty, Space, Table, Tag, Typography } from 'antd'
import {
  DeleteOutlined,
  MoreOutlined,
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
import { tableRowNavigateProps } from '../../../shared/lib/tableRowNavigate'
import { auditResultLabel, projectLabel, sourceVersionLabel } from '../../../shared/lib/tablePresentation'

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
      title: '审计配置',
      dataIndex: 'task_type',
      width: 130,
      render: (v: TaskSummary['task_type'], row) => (
        <div>
          <Tag color={v === 'discovery' ? 'blue' : 'purple'}>{v === 'discovery' ? '代码审计' : '定向验证'}</Tag>
          <div><Text type="secondary" style={{ fontSize: 12 }}>优先级：{getPriorityMeta(row.priority).label}</Text></div>
        </div>
      ),
    },
    {
      title: '执行状态',
      dataIndex: 'status',
      width: 140,
      render: (v: string, row) => {
        const m = getStatusMeta(v)
        return (
          <div>
            <Tag color={m.color}>{m.label}</Tag>
            <div><Text type="secondary" style={{ fontSize: 12 }}>更新 {dayjs(row.updated_at).format('MM-DD HH:mm')}</Text></div>
          </div>
        )
      },
    },
    {
      title: '审计结果',
      dataIndex: 'verdict',
      width: 150,
      render: (v: string | null, row) => (
        <div>
          <Tag color={v ? getVerdictMeta(v).color : row.status === 'completed' ? 'green' : 'default'}>
            {auditResultLabel(row.status, v)}
          </Tag>
          {row.task_type === 'discovery' ? (
            <div><Text type="secondary" style={{ fontSize: 12 }}>线索 {row.finding_count} · 待复核 {row.pending_review_count} · 确认 {row.confirmed_count}</Text></div>
          ) : null}
          {row.report_status ? <div><Text type="secondary" style={{ fontSize: 12 }}>报告：{row.report_status === 'published' ? '已发布' : '已生成'}</Text></div> : null}
        </div>
      ),
    },
    {
      title: '发起时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      fixed: 'right',
      render: (_, row) => {
        const moreItems = []
        if (canCancel(row.status)) {
          moreItems.push({
            key: 'cancel', icon: <PauseCircleOutlined />, label: '取消运行', danger: true,
            onClick: () => modal.confirm({
              title: CONFIRM_COPY.cancel.title, content: CONFIRM_COPY.cancel.content,
              okText: CONFIRM_COPY.cancel.okText, okType: 'danger', cancelText: '返回',
              onOk: () => onCancel(row.id),
            }),
          })
        }
        if (canRetry(row.status)) {
          moreItems.push({
            key: 'retry', icon: <RedoOutlined />, label: '重新运行',
            onClick: () => modal.confirm({
              title: CONFIRM_COPY.retry.title, content: CONFIRM_COPY.retry.content,
              okText: CONFIRM_COPY.retry.okText, cancelText: '返回',
              onOk: () => onRetry(row.id),
            }),
          })
        }
        if (canDelete(row.status)) {
          moreItems.push({
            key: 'archive', icon: <DeleteOutlined />, label: '归档运行', danger: true,
            onClick: () => modal.confirm({
              title: CONFIRM_COPY.delete.title, content: CONFIRM_COPY.delete.content,
              okText: CONFIRM_COPY.delete.okText, okType: 'danger', cancelText: '返回',
              onOk: () => onDelete(row.id),
            }),
          })
        }
        return (
        <Space size="small" onClick={(e) => e.stopPropagation()}>
          <Button size="small" type="link" onClick={() => navigate(`/tasks/${row.id}?tab=progress`)}>
            {row.status === 'needs_review' ? '继续处理' : row.status === 'completed' ? '查看结果' : row.status === 'failed' ? '查看错误' : '查看进度'}
          </Button>
          {moreItems.length > 0 ? (
            <Dropdown menu={{ items: moreItems }} trigger={['click']}>
              <Button size="small" icon={<MoreOutlined />} loading={pendingId === row.id} aria-label="更多操作" />
            </Dropdown>
          ) : null}
        </Space>
        )
      },
    },
  ]

  return (
    <Table
      rowKey="id"
      loading={loading}
      columns={columns}
      dataSource={data}
      scroll={{ x: 980 }}
      locale={{
        emptyText: (
          <Empty description="暂无审计运行">
            <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
              发起代码审计
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
      onRow={(row) => tableRowNavigateProps(() => navigate(`/tasks/${row.id}?tab=progress`))}
    />
  )
}
