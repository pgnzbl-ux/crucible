import { describe, expect, it } from 'vitest'

import { TREND_SAMPLE_NOTE, trendChartAriaLabel } from './TaskTrendChart'

describe('TaskTrendChart copy', () => {
  it('标明趋势来自最近 200 条而不是全量', () => {
    expect(TREND_SAMPLE_NOTE).toMatch(/200/)
    expect(trendChartAriaLabel('08-18 1 个')).toContain('最近 200')
  })
})
