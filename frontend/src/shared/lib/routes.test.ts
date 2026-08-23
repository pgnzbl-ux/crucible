import { describe, expect, it } from 'vitest'

import { getRouteMeta } from './routes'

describe('audit-first route metadata', () => {
  it('uses code-audit product language for primary routes', () => {
    expect(getRouteMeta('/tasks').title).toBe('代码审计')
    expect(getRouteMeta('/projects').title).toBe('项目资产')
    expect(getRouteMeta('/reports').title).toBe('审计报告')
  })

  it('provides breadcrumbs for finding list and detail', () => {
    expect(getRouteMeta('/findings').title).toBe('漏洞线索')
    expect(getRouteMeta('/findings/group-1').breadcrumb.at(-1)?.label).toBe('详情')
  })
})
