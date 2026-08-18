import { afterEach, describe, expect, it } from 'vitest'

import { resetTaskActionLocks, tryLockTaskAction, unlockTaskAction } from './taskActionLock'

describe('taskActionLock', () => {
  afterEach(() => {
    resetTaskActionLocks()
  })

  it('rejects a second lock on the same task', () => {
    expect(tryLockTaskAction('t1')).toBe(true)
    expect(tryLockTaskAction('t1')).toBe(false)
    expect(tryLockTaskAction('t2')).toBe(true)
    unlockTaskAction('t1')
    expect(tryLockTaskAction('t1')).toBe(true)
  })
})
