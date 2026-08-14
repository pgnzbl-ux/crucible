import { describe, expect, it } from 'vitest'
import { canMutateLab } from './labUi'

describe('canMutateLab', () => {
  it('blocks when tasks are using the lab', () => {
    expect(canMutateLab(1)).toBe(false)
    expect(canMutateLab(0)).toBe(true)
  })
})
