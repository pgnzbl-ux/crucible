import { describe, expect, it } from 'vitest'
import {
  canDestroyLab,
  canMutateLab,
  canRebuildLab,
  canStartLab,
  canStopLab,
  isLabTtlActive,
  shouldPollLabs,
  showDestroyLab,
  showRebuildLab,
  showStartLab,
  showStopLab,
} from './labUi'

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

  it('polls expired labs so docker drift is visible', () => {
    expect(shouldPollLabs([{ labs: [{ status: 'expired' }] }])).toBe(true)
    expect(shouldPollLabs([])).toBe(false)
  })
})

describe('lab action gates', () => {
  it('only starts stopped or expired labs and only stops ready labs', () => {
    expect(canStartLab('stopped', 0)).toBe(true)
    expect(canStartLab('expired', 0)).toBe(true)
    expect(canStartLab('ready', 0)).toBe(false)
    expect(canStopLab('ready', 0)).toBe(true)
    expect(canStopLab('stopped', 0)).toBe(false)
    expect(canStartLab('stopped', 1)).toBe(false)
  })

  it('lets destroy abort creating even when a task occupies the lab', () => {
    expect(canDestroyLab('creating', 1)).toBe(true)
    expect(canDestroyLab('creating', 0)).toBe(true)
    expect(canRebuildLab('creating', 1)).toBe(false)
    expect(canRebuildLab('creating', 0)).toBe(true)
  })

  it('blocks rebuild and destroy when a ready lab is occupied', () => {
    expect(canDestroyLab('ready', 1)).toBe(false)
    expect(canRebuildLab('ready', 1)).toBe(false)
    expect(canDestroyLab('ready', 0)).toBe(true)
    expect(canDestroyLab('destroyed', 0)).toBe(false)
  })

  it('blocks manual actions while rebuilding', () => {
    expect(canRebuildLab('rebuilding', 0)).toBe(false)
    expect(canDestroyLab('rebuilding', 0)).toBe(false)
    expect(canStartLab('rebuilding', 0)).toBe(false)
    expect(canStopLab('rebuilding', 0)).toBe(false)
  })
})

describe('lab action visibility', () => {
  it('hides irrelevant buttons per lifecycle state', () => {
    expect(showStopLab('ready', 0)).toBe(true)
    expect(showStartLab('ready', 0)).toBe(false)
    expect(showStartLab('stopped', 0)).toBe(true)
    expect(showStopLab('stopped', 0)).toBe(false)
    expect(showRebuildLab('expired', 0)).toBe(true)
    expect(showStartLab('expired', 0)).toBe(true)
    expect(showStopLab('expired', 0)).toBe(false)
    expect(showRebuildLab('destroyed', 0)).toBe(true)
    expect(showDestroyLab('destroyed', 0)).toBe(false)
    expect(showDestroyLab('rebuilding', 0)).toBe(false)
    expect(showRebuildLab('rebuilding', 0)).toBe(false)
  })

  it('hides most actions when a live task occupies a ready lab', () => {
    expect(showStopLab('ready', 1)).toBe(false)
    expect(showRebuildLab('ready', 1)).toBe(false)
    expect(showDestroyLab('ready', 1)).toBe(false)
    expect(showDestroyLab('creating', 1)).toBe(true)
  })
})

describe('isLabTtlActive', () => {
  it('only counts down when the lab is ready or stopped', () => {
    expect(isLabTtlActive('ready')).toBe(true)
    expect(isLabTtlActive('stopped')).toBe(true)
    expect(isLabTtlActive('creating')).toBe(false)
    expect(isLabTtlActive('failed')).toBe(false)
    expect(isLabTtlActive('expired')).toBe(false)
    expect(isLabTtlActive('destroyed')).toBe(false)
    expect(isLabTtlActive('rebuilding')).toBe(false)
  })
})
