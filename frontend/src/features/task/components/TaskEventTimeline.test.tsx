import { describe, expect, it } from 'vitest'

import { isEventDetailsDefaultOpen } from './TaskEventTimeline'

describe('isEventDetailsDefaultOpen', () => {
  it('keeps thinking and successful tool details collapsed', () => {
    expect(isEventDetailsDefaultOpen('agent.thinking', {})).toBe(false)
    expect(isEventDetailsDefaultOpen('tool.call.started', {})).toBe(false)
    expect(isEventDetailsDefaultOpen('tool.call.completed', { is_error: false })).toBe(false)
  })

  it('keeps failed and denied tool details expanded', () => {
    expect(isEventDetailsDefaultOpen('tool.call.completed', { is_error: true })).toBe(true)
    expect(isEventDetailsDefaultOpen('tool.call.denied', {})).toBe(true)
  })
})
