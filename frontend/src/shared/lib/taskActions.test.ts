import { describe, expect, it } from 'vitest'

import {
  canCancel,
  canDelete,
  canRetry,
  CONFIRM_COPY,
  defaultTaskDetailTab,
  shouldFetchTaskReport,
} from './taskActions'

describe('taskActions', () => {
  it.each([
    ['pending', true],
    ['queued', true],
    ['running', true],
    ['failed', false],
    ['completed', false],
  ])('canCancel(%s) → %s', (status, expected) => {
    expect(canCancel(status)).toBe(expected)
  })

  it.each([
    ['failed', true],
    ['cancelled', true],
    ['completed', true],
    ['needs_review', true],
    ['running', false],
    ['queued', false],
  ])('canRetry(%s) → %s', (status, expected) => {
    expect(canRetry(status)).toBe(expected)
  })

  it.each([
    ['completed', true],
    ['failed', true],
    ['cancelled', true],
    ['needs_review', true],
    ['running', false],
    ['pending', false],
    ['queued', false],
    ['archived', false],
  ])('canDelete(%s) → %s', (status, expected) => {
    expect(canDelete(status)).toBe(expected)
  })

  it('default tab is progress for active tasks and when unspecified', () => {
    expect(defaultTaskDetailTab('running')).toBe('progress')
    expect(defaultTaskDetailTab('queued')).toBe('progress')
    expect(defaultTaskDetailTab('completed')).toBe('progress')
  })

  it.each([
    ['completed', true],
    ['needs_review', true],
    ['failed', false],
    ['running', false],
    ['queued', false],
    ['cancelled', false],
  ])('shouldFetchTaskReport(%s) → %s', (status, expected) => {
    expect(shouldFetchTaskReport(status)).toBe(expected)
  })

  it('destructive confirms explain the consequence', () => {
    expect(CONFIRM_COPY.cancel.content).toMatch(/沙箱/)
    expect(CONFIRM_COPY.delete.content).toMatch(/报告/)
    expect(CONFIRM_COPY.retry.content).toMatch(/第一步|整条重跑/)
  })
})
