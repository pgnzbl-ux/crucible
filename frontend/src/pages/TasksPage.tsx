import { useState } from 'react'
import { App, Button, Space } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../shared/lib/api'
import { AppLayout } from '../app/layout'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { TaskFilterBar } from '../features/task/components/TaskFilterBar'
import { TaskTable } from '../features/task/components/TaskTable'
import { TaskCreateDrawer } from '../features/task/components/TaskCreateDrawer'
import { useTaskListParams } from '../features/task/hooks/useTaskListParams'

export function TasksPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const { params, setParams, clearParams } = useTaskListParams()

  const apiParams: Record<string, string> = { limit: '100' }
  if (params.status) apiParams.status = params.status
  if (params.priority) apiParams.priority = params.priority
  if (params.q) apiParams.q = params.q
  if (params.dateFrom) apiParams.date_from = params.dateFrom
  if (params.dateTo) apiParams.date_to = params.dateTo

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['tasks', params],
    queryFn: () => api.listTasks(apiParams),
    refetchInterval: 5000,
  })

  const cancelMutation = useMutation({
    mutationFn: (id: string) => api.cancelTask(id),
    onSuccess: () => {
      message.success('任务已取消')
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const retryMutation = useMutation({
    mutationFn: (id: string) => api.retryTask(id),
    onSuccess: () => {
      message.success('任务已重新提交(断点续跑)')
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteTask(id),
    onSuccess: () => {
      message.success('任务已删除')
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  return (
    <AppLayout>
      <PageHeader
        title="任务管理"
        subtitle="提交漏洞验证任务，Agent 将在隔离沙箱中自动分析"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建任务
            </Button>
          </Space>
        }
      />

      <TaskFilterBar params={params} onChange={setParams} onClear={clearParams} />

      <PageContainer>
        <TaskTable
          data={data?.items ?? []}
          loading={isLoading}
          total={data?.total ?? 0}
          onCancel={(id) => cancelMutation.mutate(id)}
          onRetry={(id) => retryMutation.mutate(id)}
          onDelete={(id) => deleteMutation.mutate(id)}
          cancelPending={cancelMutation.isPending}
          retryPending={retryMutation.isPending}
          deletePending={deleteMutation.isPending}
        />
      </PageContainer>

      <TaskCreateDrawer open={createOpen} onClose={() => setCreateOpen(false)} />
    </AppLayout>
  )
}
