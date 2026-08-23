import { describe, expect, it } from 'vitest'

import {
  buildFindingsSearch,
  parseFindingProgress,
  parseFindingScope,
  progressToParams,
} from './findingsListQuery'

describe('findingsListQuery', () => {
  it('omits default focus scope and page 1', () => {
    expect(buildFindingsSearch({ scope: 'focus', page: 1 })).toBe('/findings')
  })

  it('keeps work-queue and search shareable', () => {
    expect(buildFindingsSearch({
      scope: 'review',
      q: 'db.py',
      engine: 'semgrep',
      page: 2,
    })).toBe('/findings?scope=review&q=db.py&engine=semgrep&page=2')
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
      resolution: 'false_positive',
      page: 1,
    })).toBe('/findings?scope=all&resolution=false_positive')
  })

  it('parses scope and status / resolution deep links', () => {
    expect(parseFindingScope(new URLSearchParams('scope=review'))).toBe('review')
    expect(parseFindingScope(new URLSearchParams('status=dispatched'))).toBe('all')
    expect(parseFindingScope(new URLSearchParams('resolution=confirmed'))).toBe('all')
    expect(parseFindingScope(new URLSearchParams())).toBe('focus')
  })

  it('parses progress from status or resolution', () => {
    expect(parseFindingProgress(new URLSearchParams('status=dispatched'))).toBe('status:dispatched')
    expect(parseFindingProgress(new URLSearchParams('resolution=confirmed'))).toBe('resolution:confirmed')
    expect(parseFindingProgress(new URLSearchParams('status=resolved'))).toBeUndefined()
    expect(progressToParams('resolution:ignored')).toEqual({ resolution: 'ignored' })
    expect(progressToParams('status:needs_review')).toEqual({ status: 'needs_review' })
  })
})
