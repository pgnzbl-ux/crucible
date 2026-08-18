import { describe, expect, it } from 'vitest'

import { canConfigureConcurrentTasks, shouldShowSoloWorkerAlert } from './RuntimePanel'

describe('shouldShowSoloWorkerAlert', () => {
  it('shows alert on solo worker pool', () => {
    expect(shouldShowSoloWorkerAlert('solo')).toBe(true)
    expect(shouldShowSoloWorkerAlert('prefork')).toBe(false)
  })
})

describe('canConfigureConcurrentTasks', () => {
  it('allows editing only on Linux prefork', () => {
    expect(canConfigureConcurrentTasks('prefork')).toBe(true)
    expect(canConfigureConcurrentTasks('solo')).toBe(false)
  })
})
