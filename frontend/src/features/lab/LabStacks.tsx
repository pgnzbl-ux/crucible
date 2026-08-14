import { useEffect, useState, type ReactNode } from 'react'
import { App, Button, Collapse, Empty, Popconfirm, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  type Lab,
  type LabAction,
  type LabContainer,
  type LabContainerAction,
} from '../../shared/lib/api'
import { canMutateLab } from './labUi'

const { Link, Text } = Typography
const OCCUPIED_TIP = '有验证任务占用，请先取消任务'

function statusColor(status: string) {
  if (status === 'running') return 'green'
  if (status === 'failed' || status === 'error') return 'red'
  if (status === 'starting' || status === 'rebuilding') return 'processing'
  return 'default'
}

function formatTtl(seconds: number) {
  if (seconds <= 0) return '已到期'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  return [hours, minutes, secs].map((value) => String(value).padStart(2, '0')).join(':')
}

function TtlCountdown({ seconds }: { seconds: number }) {
  const [remaining, setRemaining] = useState(seconds)

  useEffect(() => {
    setRemaining(seconds)
    const timer = window.setInterval(() => setRemaining((value) => Math.max(0, value - 1)), 1000)
    return () => window.clearInterval(timer)
  }, [seconds])

  return <Text type={remaining === 0 ? 'danger' : undefined}>{formatTtl(remaining)}</Text>
}

function GuardedAction({
  disabled,
  children,
}: {
  disabled: boolean
  children: ReactNode
}) {
  return disabled ? (
    <Tooltip title={OCCUPIED_TIP}>
      <span>{children}</span>
    </Tooltip>
  ) : (
    children
  )
}

type MutationInput =
  | { kind: 'lab-action'; labId: string; action: LabAction }
  | { kind: 'lab-delete'; labId: string }
  | { kind: 'container-action'; labId: string; containerName: string; action: LabContainerAction }
  | { kind: 'container-delete'; labId: string; containerName: string }

export function LabStacks() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['labs'],
    queryFn: () => api.listLabs(),
  })

  const mutation = useMutation({
    mutationFn: (input: MutationInput) => {
      if (input.kind === 'lab-action') return api.labAction(input.labId, input.action)
      if (input.kind === 'lab-delete') return api.deleteLab(input.labId)
      if (input.kind === 'container-action') {
        return api.labContainerAction(input.labId, input.containerName, input.action)
      }
      return api.deleteLabContainer(input.labId, input.containerName)
    },
    onSuccess: async () => {
      message.success('操作已提交')
      await queryClient.invalidateQueries({ queryKey: ['labs'] })
    },
    onError: (error) => message.error(error.message),
  })

  const actionButton = (
    lab: Lab,
    label: string,
    input: MutationInput,
    danger = false,
    confirmation?: string,
  ) => {
    const occupied = !canMutateLab(lab.live_task_count ?? 0)
    const button = (
      <Button
        size="small"
        danger={danger}
        disabled={occupied}
        loading={mutation.isPending && mutation.variables === input}
        onClick={confirmation ? undefined : () => mutation.mutate(input)}
      >
        {label}
      </Button>
    )
    const guarded = <GuardedAction disabled={occupied}>{button}</GuardedAction>
    return confirmation && !occupied ? (
      <Popconfirm title={confirmation} onConfirm={() => mutation.mutate(input)}>
        {guarded}
      </Popconfirm>
    ) : (
      guarded
    )
  }

  const containerColumns = (lab: Lab): ColumnsType<LabContainer> => [
    { title: '容器', dataIndex: 'name', width: 180 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (status: string) => <Tag color={statusColor(status)}>{status}</Tag>,
    },
    {
      title: '端口',
      dataIndex: 'ports',
      width: 180,
      render: (ports: string) => ports || '—',
    },
    { title: '镜像', dataIndex: 'image', ellipsis: true },
    {
      title: '操作',
      width: 260,
      render: (_, container) => (
        <Space size={4}>
          {actionButton(lab, '停止', {
            kind: 'container-action',
            labId: lab.id,
            containerName: container.name,
            action: 'stop',
          })}
          {actionButton(lab, '启动', {
            kind: 'container-action',
            labId: lab.id,
            containerName: container.name,
            action: 'start',
          })}
          {actionButton(lab, '重启', {
            kind: 'container-action',
            labId: lab.id,
            containerName: container.name,
            action: 'restart',
          })}
          {actionButton(
            lab,
            '删除',
            { kind: 'container-delete', labId: lab.id, containerName: container.name },
            true,
            `确定删除容器 ${container.name}？`,
          )}
        </Space>
      ),
    },
  ]

  const labColumns: ColumnsType<Lab> = [
    {
      title: '提交',
      dataIndex: 'commit_sha',
      width: 110,
      render: (sha: string) => <Text code>{sha.slice(0, 8)}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (status: string) => <Tag color={statusColor(status)}>{status}</Tag>,
    },
    {
      title: '访问地址',
      dataIndex: 'target_url',
      render: (url: string | null) =>
        url ? (
          <Link href={url} target="_blank" rel="noreferrer">
            {url}
          </Link>
        ) : (
          '—'
        ),
    },
    {
      title: '剩余时间',
      dataIndex: 'ttl_remaining_seconds',
      width: 120,
      render: (seconds: number) => <TtlCountdown seconds={seconds} />,
    },
    {
      title: '占用任务',
      dataIndex: 'live_task_count',
      width: 90,
      render: (count: number) => count ?? 0,
    },
    {
      title: '操作',
      width: 280,
      render: (_, lab) => (
        <Space size={4}>
          {actionButton(lab, '停止', { kind: 'lab-action', labId: lab.id, action: 'stop' })}
          {actionButton(lab, '启动', { kind: 'lab-action', labId: lab.id, action: 'start' })}
          {actionButton(
            lab,
            '重建',
            { kind: 'lab-action', labId: lab.id, action: 'rebuild' },
            false,
            '确定重建该靶场？',
          )}
          {actionButton(
            lab,
            '销毁',
            { kind: 'lab-delete', labId: lab.id },
            true,
            '确定销毁该靶场？',
          )}
        </Space>
      ),
    },
  ]

  if (!isLoading && !(data?.items.length)) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无靶场" />
  }

  return (
    <Collapse
      defaultActiveKey={data?.items.map((group) => group.project_id)}
      items={(data?.items ?? []).map((group) => ({
        key: group.project_id,
        label: (
          <Space>
            <Text strong>{group.project_name}</Text>
            <Tag>{group.labs.length} 个靶场</Tag>
          </Space>
        ),
        children: (
          <Table
            rowKey="id"
            loading={isLoading}
            dataSource={group.labs}
            columns={labColumns}
            pagination={false}
            scroll={{ x: 1080 }}
            expandable={{
              expandedRowRender: (lab) => (
                <>
                  {lab.error_message ? (
                    <Text type="danger" style={{ display: 'block', marginBottom: 12 }}>
                      {lab.error_message}
                    </Text>
                  ) : null}
                  <Table
                    rowKey="name"
                    size="small"
                    dataSource={lab.containers}
                    columns={containerColumns(lab)}
                    pagination={false}
                  />
                </>
              ),
              rowExpandable: (lab) => lab.containers.length > 0 || Boolean(lab.error_message),
            }}
          />
        ),
      }))}
    />
  )
}
