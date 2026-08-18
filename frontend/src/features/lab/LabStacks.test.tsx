import { describe, expect, it } from 'vitest'
import { canMutateLab, shouldPollLabs } from './labUi'

describe('canMutateLab', () => {
  it('blocks when tasks are using the lab', () => {
    expect(canMutateLab(1)).toBe(false)
    expect(canMutateLab(0)).toBe(true)
  })
})

describe('shouldPollLabs', () => {
  it('polls creating, starting and rebuilding labs', () => {
    expect(shouldPollLabs([{ labs: [{ status: 'ready' }] }])).toBe(false)
    expect(shouldPollLabs([{ labs: [{ status: 'creating' }] }])).toBe(true)
    expect(shouldPollLabs([{ labs: [{ status: 'starting' }] }])).toBe(true)
    expect(shouldPollLabs([{ labs: [{ status: 'rebuilding' }] }])).toBe(true)
  })
})

