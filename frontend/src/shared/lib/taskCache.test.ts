import { describe, expect, it } from 'vitest'

import { TASK_CREATE_LOCK_ID, taskMutationCacheOps } from './taskCache'

describe('taskMutationCacheOps', () => {
  it('cancel 必须失效详情，避免列表操作后详情仍是旧终态', () => {
    expect(taskMutationCacheOps('t1', 'cancel')).toEqual({
      remove: [],
      invalidate: [
        ['task', 't1'],
        ['tasks'],
        ['task-stats'],
        ['run-nodes', 't1'],
      ],
    })
  })

  it('retry 丢掉旧报告并刷新当前 run 相关查询', () => {
    expect(taskMutationCacheOps('t1', 'retry')).toEqual({
      remove: [['task-report', 't1']],
      invalidate: [
        ['task', 't1'],
        ['tasks'],
        ['task-stats'],
        ['run-nodes', 't1'],
        ['task-events', 't1'],
      ],
    })
  })

  it('delete 移除详情相关 cache，避免回退仍像任务还在', () => {
    expect(taskMutationCacheOps('t1', 'delete')).toEqual({
      remove: [
        ['task', 't1'],
        ['run-nodes', 't1'],
        ['task-events', 't1'],
        ['task-report', 't1'],
      ],
      invalidate: [['tasks'], ['task-stats']],
    })
  })

  it('创建锁与任务 id 隔离', () => {
    expect(TASK_CREATE_LOCK_ID).toBe('task-create')
  })
})
