import { describe, expect, it } from 'vitest'
import { canMutateLab, canStartLab, canStopLab, shouldPollLabs } from './labUi'

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

  it('polls any page that already has labs so docker drift is visible', () => {
    expect(shouldPollLabs([{ labs: [{ status: 'ready' }] }])).toBe(true)
    expect(shouldPollLabs([{ labs: [{ status: 'expired' }] }])).toBe(true)
    expect(shouldPollLabs([])).toBe(false)
  })
})

describe('lab action gates', () => {
  it('only starts stopped labs and only stops ready labs', () => {
    expect(canStartLab('stopped', 0)).toBe(true)
    expect(canStartLab('expired', 0)).toBe(false)
    expect(canStartLab('ready', 0)).toBe(false)
    expect(canStopLab('ready', 0)).toBe(true)
    expect(canStopLab('stopped', 0)).toBe(false)
    expect(canStartLab('stopped', 1)).toBe(false)
  })
})

