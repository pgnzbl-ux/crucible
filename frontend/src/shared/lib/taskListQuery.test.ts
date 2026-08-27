import { describe, expect, it } from 'vitest'

import { buildTaskListApiParams, DEFAULT_PAGE_SIZE, statsPollMs, sumTaskStats, taskListPollMs } from './taskListQuery'

describe('buildTaskListApiParams', () => {
  it('maps page 1 to offset 0 with default page size 20', () => {
    expect(buildTaskListApiParams({})).toEqual({
      limit: String(DEFAULT_PAGE_SIZE),
      offset: '0',
    })
  })

  it('maps page 3 pageSize 10 to offset 20', () => {
    expect(buildTaskListApiParams({ page: 3, pageSize: 10 })).toEqual({
      limit: '10',
      offset: '20',
    })
  })

  it('forwards filters and date range to API field names', () => {
    expect(
      buildTaskListApiParams({
        status: 'pending,queued',
        priority: 'high',
        taskType: 'discovery',
        q: 'github.com',
        dateFrom: '2026-08-01',
        dateTo: '2026-08-13',
        page: 2,
        pageSize: 20,
      }),
    ).toEqual({
      limit: '20',
      offset: '20',
      status: 'pending,queued',
      priority: 'high',
      task_type: 'discovery',
      q: 'github.com',
      date_from: '2026-08-01',
      date_to: '2026-08-13',
    })
  })

  it('omits empty filters', () => {
    const params = buildTaskListApiParams({ status: undefined, q: '' })
    expect(params.status).toBeUndefined()
    expect(params.q).toBeUndefined()
  })

  it.each([
    [[{ status: 'completed' }], 'completed', false],
    [[{ status: 'running' }], undefined, 5_000],
    [[{ status: 'completed' }], undefined, 30_000],
    [[{ status: 'pending' }], 'pending,queued', 5_000],
  ] as const)('taskListPollMs(%j, %s) → %s', (items, filter, expected) => {
    expect(taskListPollMs([...items], filter)).toBe(expected)
  })

  it('sumTaskStats 把逗号状态加总', () => {
    expect(sumTaskStats({ pending: 2, queued: 3, running: 1 }, 'pending,queued')).toBe(5)
    expect(sumTaskStats({ pending: 2, queued: 3 }, undefined)).toBe(5)
  })

  it.each([
    [undefined, 15_000],
    [{}, 30_000],
    [{ completed: 4 }, 30_000],
    [{ pending: 1 }, 5_000],
    [{ running: 2, completed: 3 }, 5_000],
  ] as const)('statsPollMs(%j) → %s', (byStatus, expected) => {
    expect(statsPollMs(byStatus)).toBe(expected)
  })
})
