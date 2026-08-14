import { App, Button, Result, Space, Skeleton, Tag } from 'antd'
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  PauseCircleOutlined,
  RedoOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useRoute, useSearch } from 'wouter'

import { api } from '../shared/lib/api'
import { getStatusMeta } from '../shared/lib/meta'
import { canCancel, canDelete, canRetry, CONFIRM_COPY, defaultTaskDetailTab } from '../shared/lib/taskActions'
import type { TaskDetailTab } from '../shared/lib/taskActions'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { TaskDetailTabs } from '../features/task/components/TaskDetailTabs'

const TAB_KEYS: TaskDetailTab[] = ['overview', 'progress', 'events', 'report']

function tabFromSearch(search: string): TaskDetailTab | null {
  const raw = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search).get('tab')
  if (raw && (TAB_KEYS as string[]).includes(raw)) return raw as TaskDetailTab
  return null
}

export function TaskDetailPage() {
  const [, params] = useRoute('/tasks/:id')
  const [, navigate] = useLocation()
  const search = useSearch()
  const taskId = params?.id ?? ''
  const { message, modal } = App.useApp()
  const qc = useQueryClient()

  const { data: task, isLoading, isError } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: !!taskId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && canCancel(status) ? 3000 : false
    },
  })

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelTask(taskId),
    onSuccess: () => {
      message.success('任务已取消')
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['run-nodes', taskId] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const retryMutation = useMutation({
    mutationFn: () => api.retryTask(taskId),
    onSuccess: () => {
      message.success('任务已重新提交')
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['run-nodes', taskId] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteTask(taskId),
    onSuccess: () => {
      message.success('任务已删除')
      navigate('/tasks')
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  if (!taskId) {
    return null
  }

  const st = task ? getStatusMeta(task.status) : null
  const activeTab = tabFromSearch(search) ?? defaultTaskDetailTab(task?.status)
  const title = task?.project_address
    ? task.project_address.replace(/^https?:\/\//, '').slice(0, 48)
    : isLoading
      ? '加载中...'
      : '任务详情'

  const confirm = (kind: keyof typeof CONFIRM_COPY, onOk: () => void) => {
    const copy = CONFIRM_COPY[kind]
    modal.confirm({
      title: copy.title,
      content: copy.content,
      okText: copy.okText,
      okType: kind === 'retry' ? 'primary' : 'danger',
      cancelText: '返回',
      onOk,
    })
  }

  return (
    <AppLayout>
      <PageHeader
        title={title}
        subtitle={task ? `ID ${task.id.slice(0, 8)}` : undefined}
        extra={
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tasks')}>
            返回列表
          </Button>
        }
      />

      <PageContainer>
        {isLoading && !task ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : task ? (
          <>
            <div className="crucible-detail-sticky-bar">
              <Space>
                <Tag color={st?.color}>{st?.label}</Tag>
              </Space>
              <Space>
                {canCancel(task.status) && (
                  <Button
                    danger
                    icon={<PauseCircleOutlined />}
                    onClick={() => confirm('cancel', () => cancelMutation.mutate())}
                    loading={cancelMutation.isPending}
                  >
                    取消
                  </Button>
                )}
                {canRetry(task.status) && (
                  <Button
                    icon={<RedoOutlined />}
                    onClick={() => confirm('retry', () => retryMutation.mutate())}
                    loading={retryMutation.isPending}
                  >
                    重试
                  </Button>
                )}
                {canDelete(task.status) && (
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => confirm('delete', () => deleteMutation.mutate())}
                    loading={deleteMutation.isPending}
                  >
                    删除
                  </Button>
                )}
              </Space>
            </div>
            <TaskDetailTabs
              taskId={taskId}
              activeTab={activeTab}
              onTabChange={(key) => navigate(`/tasks/${taskId}?tab=${key}`, { replace: true })}
            />
          </>
        ) : (
          <Result
            status={isError ? 'error' : '404'}
            title={isError ? '加载失败' : '任务不存在'}
            subTitle={isError ? '请检查网络后重试，或返回任务列表。' : '该任务已删除或你没有访问权限。'}
            extra={
              <Button type="primary" onClick={() => navigate('/tasks')}>
                返回列表
              </Button>
            }
          />
        )}
      </PageContainer>
    </AppLayout>
  )
}
