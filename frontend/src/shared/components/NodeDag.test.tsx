import { describe, expect, it } from 'vitest'

import { buildNodeTip } from './NodeDag'

describe('buildNodeTip', () => {
  it('状态 / 耗时 / 摘要 / Token 明细逐行给出', () => {
    const rows = buildNodeTip({
      statusText: '已完成',
      caption: '12 组',
      duration: '3m24s',
      usage: {
        prompt_tokens: 1000,
        completion_tokens: 500,
        cache_read_input_tokens: 200,
        cache_creation_input_tokens: 50,
        total_tokens: 1750,
      },
    })
    expect(rows[0]).toEqual({ label: '状态', value: '已完成' })
    expect(rows).toContainEqual({ label: '耗时', value: '3m24s' })
    expect(rows).toContainEqual({ label: '摘要', value: '12 组' })
    const token = rows.find((row) => row.label === 'Token')
    expect(token?.value).toContain('1.8k')
    expect(token?.value).toContain('prompt 1000')
    expect(token?.value).toContain('cache_creation 50')
  })

  it('错误只取首行并截断到 120 字符', () => {
    const rows = buildNodeTip({ statusText: '失败', error: `${'x'.repeat(130)}\nsecond line` })
    expect(rows).toContainEqual({ label: '错误', value: `${'x'.repeat(120)}…` })
  })

  it('缺省字段不产生多余行', () => {
    expect(buildNodeTip({ statusText: '等待' })).toEqual([{ label: '状态', value: '等待' }])
    expect(
      buildNodeTip({ statusText: '已完成', usage: { prompt_tokens: 0, completion_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, total_tokens: 0 } }),
    ).toEqual([{ label: '状态', value: '已完成' }])
  })
})
