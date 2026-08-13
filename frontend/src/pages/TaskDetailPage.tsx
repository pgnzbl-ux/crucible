import { App, Button, Modal, Space, Skeleton, Tag } from 'antd'
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  PauseCircleOutlined,
  RedoOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useRoute } from 'wouter'

import { api } from '../shared/lib/api'
import { getStatusMeta } from '../shared/lib/meta'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { TaskDetailTabs } from '../features/task/components/TaskDetailTabs'

export function TaskDetailPage() {
  const [, params] = useRoute('/tasks/:id')
  const taskId = params?.id ?? ''
  const [, navigate] = useLocation()
  const { message } = App.useApp()
  const qc = useQueryClient()

  const { data: task, isLoading } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: !!taskId,
  })

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelTask(taskId),
    onSuccess: () => {
      message.success('任务已取消')
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const retryMutation = useMutation({
    mutationFn: () => api.retryTask(taskId),
    onSuccess: () => {
      message.success('任务已重新提交')
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
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

  return (
    <AppLayout>
      <PageHeader
        title={isLoading ? '加载中...' : `任务 ${taskId.slice(0, 8)}`}
        subtitle={task?.project_address}
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
                <span style={{ color: 'var(--crucible-text-secondary)', fontSize: 13 }}>
                  ID: {task.id}
                </span>
              </Space>
              <Space>
                {['queued', 'running', 'pending'].includes(task.status) && (
                  <Button
                    danger
                    icon={<PauseCircleOutlined />}
                    onClick={() => cancelMutation.mutate()}
                    loading={cancelMutation.isPending}
                  >
                    取消
                  </Button>
                )}
                {['failed', 'cancelled', 'completed', 'needs_review'].includes(task.status) && (
                  <Button
                    icon={<RedoOutlined />}
                    onClick={() => retryMutation.mutate()}
                    loading={retryMutation.isPending}
                  >
                    重试
                  </Button>
                )}
                {!['running', 'pending', 'queued'].includes(task.status) && (
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => {
                      Modal.confirm({
                        title: '删除任务',
                        content: '任务及其运行记录将被归档(软删)。确定继续?',
                        okText: '删除',
                        okType: 'danger',
                        cancelText: '取消',
                        onOk: () => deleteMutation.mutate(),
                      })
                    }}
                    loading={deleteMutation.isPending}
                  >
                    删除
                  </Button>
                )}
              </Space>
            </div>
            <TaskDetailTabs taskId={taskId} />
          </>
        ) : (
          <div>任务不存在</div>
        )}
      </PageContainer>
    </AppLayout>
  )
}
