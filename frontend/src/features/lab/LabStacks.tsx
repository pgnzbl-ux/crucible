import { useEffect, useState, type ReactNode } from 'react'
import { App, Button, Collapse, Empty, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  type Lab,
  type LabAction,
  type LabContainer,
  type LabContainerAction,
} from '../../shared/lib/api'
import { safeHttpUrl } from '../../shared/lib/safeUrl'
import { useErrorToast } from '../../shared/hooks/useErrorToast'
import { canDestroyLab, canMutateLab, canRebuildLab, canStartLab, canStopLab, shouldPollLabs, showDestroyLab, showRebuildLab, showStartLab, showStopLab } from './labUi'

const { Link, Text } = Typography
const OCCUPIED_TIP = '有验证任务占用，请先取消任务'

function canMutateContainer(status: string, liveTaskCount: number): boolean {
  return (status === 'ready' || status === 'stopped') && canMutateLab(liveTaskCount)
}

function showMutateContainer(status: string, liveTaskCount: number): boolean {
  return canMutateContainer(status, liveTaskCount)
}

function statusColor(status: string) {
  if (status === 'running' || status === 'ready' || status.toLowerCase().startsWith('up')) return 'green'
  if (status === 'failed' || status === 'error') return 'red'
  if (status === 'creating' || status === 'starting' || status === 'rebuilding') return 'processing'
  if (status === 'expired') return 'orange'
  return 'default'
}

const LAB_STATUS_LABELS: Record<string, string> = {
  creating: '正在创建',
  ready: '可用于验证',
  stopped: '已停止',
  rebuilding: '正在重建',
  failed: '创建失败',
  expired: '已到期',
  destroyed: '已销毁',
}

function containerStatusLabel(status: string) {
  const normalized = status.toLowerCase()
  if (normalized.startsWith('up') || normalized === 'running') return '运行中'
  if (normalized.includes('restart')) return '重启中'
  if (normalized.includes('exit') || normalized === 'stopped') return '已停止'
  return status || '未知'
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

function mutationRowKey(input: MutationInput) {
  return 'containerName' in input
    ? `container:${input.labId}:${input.containerName}`
    : `lab:${input.labId}`
}

function mutationActionKey(input: MutationInput) {
  const action = 'action' in input ? input.action : 'delete'
  return `${mutationRowKey(input)}:${action}`
}

export function LabStacks() {
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const { data, error, isError, isLoading } = useQuery({
    queryKey: ['labs'],
    queryFn: () => api.listLabs(),
    refetchInterval: (query) => (shouldPollLabs(query.state.data?.items) ? 3000 : false),
  })
  useErrorToast(isError, error, '靶场列表加载失败')

  const mutation = useMutation({
    mutationFn: (input: MutationInput) => {
      if (input.kind === 'lab-action') return api.labAction(input.labId, input.action)
      if (input.kind === 'lab-delete') return api.deleteLab(input.labId)
      if (input.kind === 'container-action') {
        return api.labContainerAction(input.labId, input.containerName, input.action)
      }
      return api.deleteLabContainer(input.labId, input.containerName)
    },
    onSuccess: async (_data, input) => {
      if (input.kind === 'lab-action' && input.action === 'rebuild') {
        message.success('靶场重建完成')
      } else {
        message.success('操作已提交')
      }
      await queryClient.invalidateQueries({ queryKey: ['labs'] })
    },
    onError: (error) => message.error(error.message),
  })

  const actionButton = (
    lab: Lab,
    label: string,
    input: MutationInput,
    canAct: (status: string, liveTaskCount: number) => boolean,
    show: (status: string, liveTaskCount: number) => boolean,
    danger = false,
    confirmation?: string,
  ) => {
    const live = lab.live_task_count ?? 0
    if (!show(lab.status, live)) return null
    const allowed = canAct(lab.status, live)
    const statusOk = canAct(lab.status, 0)
    const occupiedTip = !allowed && statusOk
    const pendingSameRow =
      mutation.isPending &&
      mutation.variables !== undefined &&
      mutationRowKey(mutation.variables) === mutationRowKey(input)
    const loading =
      mutation.isPending &&
      mutation.variables !== undefined &&
      mutationActionKey(mutation.variables) === mutationActionKey(input)
    const runAction = () => mutation.mutateAsync(input)
    const handleClick = () => {
      if (!allowed || pendingSameRow) return
      if (confirmation) {
        modal.confirm({
          title: confirmation,
          content:
            input.kind === 'lab-action' && input.action === 'rebuild'
              ? '将重新拉取源码与配方并执行 docker compose up --build，可能需要数分钟。'
              : undefined,
          okText: '确定',
          cancelText: '取消',
          okButtonProps: danger ? { danger: true } : undefined,
          onOk: runAction,
        })
        return
      }
      void runAction()
    }
    const button = (
      <Button
        size="small"
        danger={danger}
        disabled={!allowed || pendingSameRow}
        loading={loading}
        onClick={handleClick}
      >
        {label}
      </Button>
    )
    return <GuardedAction disabled={occupiedTip}>{button}</GuardedAction>
  }

  const containerColumns = (lab: Lab): ColumnsType<LabContainer> => [
    { title: '容器服务', dataIndex: 'name', width: 180 },
    {
      title: '运行状态',
      dataIndex: 'status',
      width: 110,
      render: (status: string) => <Tag color={statusColor(status)}>{containerStatusLabel(status)}</Tag>,
    },
    {
      title: '暴露端口',
      dataIndex: 'ports',
      width: 180,
      render: (ports: string) => ports || '—',
    },
    { title: '镜像', dataIndex: 'image', ellipsis: true },
    {
      title: '操作',
      width: 260,
      render: (_, container) => (
        <Space size={4} onClick={(event) => event.stopPropagation()}>
          {actionButton(
            lab,
            '停止',
            {
              kind: 'container-action',
              labId: lab.id,
              containerName: container.name,
              action: 'stop',
            },
            canMutateContainer,
            showMutateContainer,
          )}
          {actionButton(
            lab,
            '启动',
            {
              kind: 'container-action',
              labId: lab.id,
              containerName: container.name,
              action: 'start',
            },
            canMutateContainer,
            showMutateContainer,
          )}
          {actionButton(
            lab,
            '重启',
            {
              kind: 'container-action',
              labId: lab.id,
              containerName: container.name,
              action: 'restart',
            },
            canMutateContainer,
            showMutateContainer,
          )}
          {actionButton(
            lab,
            '删除',
            { kind: 'container-delete', labId: lab.id, containerName: container.name },
            canMutateContainer,
            showMutateContainer,
            true,
            `确定删除容器 ${container.name}？`,
          )}
        </Space>
      ),
    },
  ]

  const labColumns: ColumnsType<Lab> = [
    {
      title: '源码版本',
      dataIndex: 'commit_sha',
      width: 110,
      render: (sha: string, lab) => (
        <div>
          <Text code>{sha.slice(0, 8)}</Text>
          <div><Text type="secondary" style={{ fontSize: 12 }}>{lab.containers.length} 个容器服务</Text></div>
        </div>
      ),
    },
    {
      title: '可用状态',
      dataIndex: 'status',
      width: 110,
      render: (status: string, lab) => {
        const running = lab.containers.filter((item) => containerStatusLabel(item.status) === '运行中').length
        return (
          <div>
            <Tag color={statusColor(status)}>{LAB_STATUS_LABELS[status] ?? status}</Tag>
            <div><Text type="secondary" style={{ fontSize: 12 }}>容器健康 {running}/{lab.containers.length}</Text></div>
          </div>
        )
      },
    },
    {
      title: '访问地址',
      dataIndex: 'target_url',
      render: (url: string | null) => {
        const href = safeHttpUrl(url)
        if (href) {
          return (
            <Link href={href} target="_blank" rel="noopener noreferrer">
              {url}
            </Link>
          )
        }
        return url || '—'
      },
    },
    {
      title: '自动到期',
      dataIndex: 'ttl_remaining_seconds',
      width: 120,
      render: (seconds: number | null) =>
        seconds == null ? '—' : <TtlCountdown seconds={seconds} />,
    },
    {
      title: '使用情况',
      dataIndex: 'live_task_count',
      width: 90,
      render: (count: number) => count > 0 ? <Tag color="processing">{count} 个审计正在使用</Tag> : <Tag color="green">空闲</Tag>,
    },
    {
      title: '操作',
      width: 280,
      render: (_, lab) => (
        <Space size={4} onClick={(event) => event.stopPropagation()}>
          {actionButton(lab, '停止', { kind: 'lab-action', labId: lab.id, action: 'stop' }, canStopLab, showStopLab)}
          {actionButton(lab, '启动', { kind: 'lab-action', labId: lab.id, action: 'start' }, canStartLab, showStartLab)}
          {actionButton(
            lab,
            '重建',
            { kind: 'lab-action', labId: lab.id, action: 'rebuild' },
            canRebuildLab,
            showRebuildLab,
            false,
            '确定重建该靶场？',
          )}
          {actionButton(
            lab,
            '销毁',
            { kind: 'lab-delete', labId: lab.id },
            canDestroyLab,
            showDestroyLab,
            true,
            '确定销毁该靶场？',
          )}
        </Space>
      ),
    },
  ]

  if (!isLoading && !(data?.items.length)) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={isError ? '靶场列表加载失败' : '暂无靶场'}
      />
    )
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
