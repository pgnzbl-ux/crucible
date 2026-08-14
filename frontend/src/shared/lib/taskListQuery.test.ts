import { describe, expect, it } from 'vitest'

import { buildTaskListApiParams, DEFAULT_PAGE_SIZE } from './taskListQuery'

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
})
