import { describe, expect, it } from 'vitest'

import {
  buildFindingsSearch,
  parseFindingProgress,
  parseFindingScope,
  progressToParams,
} from './findingsListQuery'

describe('findingsListQuery', () => {
  it('omits default workbench scope and page 1', () => {
    expect(buildFindingsSearch({ scope: 'workbench', page: 1 })).toBe('/findings')
  })

  it('keeps work-queue and search shareable', () => {
    expect(buildFindingsSearch({
      scope: 'verifying',
      q: 'db.py',
      engine: 'semgrep',
      page: 2,
    })).toBe('/findings?scope=verifying&q=db.py&engine=semgrep&page=2')
  })

  it('preserves dashboard deep-link status without forcing scope', () => {
    expect(buildFindingsSearch({
      scope: 'all',
      status: 'dispatched',
      page: 1,
    })).toBe('/findings?scope=all&status=dispatched')
  })

  it('writes resolution deep links for closed outcomes', () => {
    expect(buildFindingsSearch({
      scope: 'all',
      resolution: 'confirmed',
      page: 1,
    })).toBe('/findings?scope=all&resolution=confirmed')
  })

  it('prefers resolution over status when both provided', () => {
    expect(buildFindingsSearch({
      scope: 'all',
      status: 'resolved',
      resolution: 'code_reachable',
      page: 1,
    })).toBe('/findings?scope=all&resolution=code_reachable')
  })

  it('parses scope and status / resolution deep links', () => {
    expect(parseFindingScope(new URLSearchParams('scope=verifying'))).toBe('verifying')
    expect(parseFindingScope(new URLSearchParams('status=dispatched'))).toBe('all')
    expect(parseFindingScope(new URLSearchParams('resolution=confirmed'))).toBe('all')
    expect(parseFindingScope(new URLSearchParams())).toBe('workbench')
  })

  it('parses progress from status or resolution', () => {
    expect(parseFindingProgress(new URLSearchParams('status=dispatched'))).toBe('status:dispatched')
    expect(parseFindingProgress(new URLSearchParams('resolution=confirmed'))).toBe('resolution:confirmed')
    expect(parseFindingProgress(new URLSearchParams('status=resolved'))).toBeUndefined()
    expect(progressToParams('resolution:code_reachable')).toEqual({ resolution: 'code_reachable' })
    expect(progressToParams('status:dispatched')).toEqual({ status: 'dispatched' })
  })
})
