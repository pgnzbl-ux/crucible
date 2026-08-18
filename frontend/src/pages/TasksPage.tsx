import { App, Button, Space } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../shared/lib/api'
import { buildTaskListApiParams, DEFAULT_PAGE_SIZE, taskListPollMs } from '../shared/lib/taskListQuery'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { TaskFilterBar } from '../features/task/components/TaskFilterBar'
import { TaskTable } from '../features/task/components/TaskTable'
import { TaskCreateDrawer } from '../features/task/components/TaskCreateDrawer'
import { useTaskListParams } from '../features/task/hooks/useTaskListParams'
import { tryLockTaskAction, unlockTaskAction } from '../shared/lib/taskActionLock'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { applyTaskMutationCache } from '../shared/lib/taskCache'

export function TasksPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { params, setParams, clearParams } = useTaskListParams()

  const page = params.page ?? 1
  const pageSize = params.pageSize ?? DEFAULT_PAGE_SIZE
  const createOpen = params.create === true

  const apiParams = buildTaskListApiParams({
    status: params.status,
    priority: params.priority,
    q: params.q,
    dateFrom: params.dateFrom,
    dateTo: params.dateTo,
    page,
    pageSize,
  })

  const { data, error, isError, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['tasks', apiParams],
    queryFn: () => api.listTasks(apiParams),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => taskListPollMs(query.state.data?.items ?? [], params.status),
  })
  useErrorToast(isError, error, '任务列表加载失败')

  const cancelMutation = useMutation({
    mutationFn: (id: string) => api.cancelTask(id),
    onMutate: (id) => {
      if (!tryLockTaskAction(id)) throw new Error('请等待当前操作完成')
      return { locked: true as const }
    },
    onSuccess: (_data, id) => {
      message.success('任务已取消')
      applyTaskMutationCache(qc, id, 'cancel')
    },
    onError: (e: Error) => {
      if (e.message !== '请等待当前操作完成') message.error(e.message)
    },
    onSettled: (_data, _error, id, ctx) => {
      if (ctx?.locked) unlockTaskAction(id)
    },
  })

  const retryMutation = useMutation({
    mutationFn: (id: string) => api.retryTask(id),
    onMutate: (id) => {
      if (!tryLockTaskAction(id)) throw new Error('请等待当前操作完成')
      return { locked: true as const }
    },
    onSuccess: (_data, id) => {
      message.success('任务已重新提交，将从源码获取开始重跑')
      applyTaskMutationCache(qc, id, 'retry')
    },
    onError: (e: Error) => {
      if (e.message !== '请等待当前操作完成') message.error(e.message)
    },
    onSettled: (_data, _error, id, ctx) => {
      if (ctx?.locked) unlockTaskAction(id)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteTask(id),
    onMutate: (id) => {
      if (!tryLockTaskAction(id)) throw new Error('请等待当前操作完成')
      return { locked: true as const }
    },
    onSuccess: (_data, id) => {
      message.success('任务已删除')
      applyTaskMutationCache(qc, id, 'delete')
    },
    onError: (e: Error) => {
      if (e.message !== '请等待当前操作完成') message.error(e.message)
    },
    onSettled: (_data, _error, id, ctx) => {
      if (ctx?.locked) unlockTaskAction(id)
    },
  })

  const pendingId =
    (cancelMutation.isPending && cancelMutation.variables) ||
    (retryMutation.isPending && retryMutation.variables) ||
    (deleteMutation.isPending && deleteMutation.variables) ||
    null

  return (
    <>
      <PageHeader
        title="任务管理"
        subtitle="提交漏洞验证任务，Agent 将在隔离沙箱中自动分析"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setParams({ create: true })}>
              新建任务
            </Button>
          </Space>
        }
      />

      <TaskFilterBar params={params} onChange={setParams} onClear={clearParams} />

      <PageContainer>
        <TaskTable
          data={data?.items ?? []}
          loading={isLoading || isFetching}
          total={data?.total ?? 0}
          page={page}
          pageSize={pageSize}
          onPageChange={(nextPage, nextSize) => setParams({ page: nextPage, pageSize: nextSize })}
          onCancel={(id) => cancelMutation.mutate(id)}
          onRetry={(id) => retryMutation.mutate(id)}
          onDelete={(id) => deleteMutation.mutate(id)}
          onCreate={() => setParams({ create: true })}
          pendingId={pendingId}
        />
      </PageContainer>

      <TaskCreateDrawer open={createOpen} onClose={() => setParams({ create: undefined })} />
    </>
  )
}
