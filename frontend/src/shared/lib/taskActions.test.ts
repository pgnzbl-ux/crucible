import { describe, expect, it } from 'vitest'

import {
  canCancel,
  canDelete,
  canRetry,
  canRetryFromNode,
  CONFIRM_COPY,
  defaultTaskDetailTab,
  shouldFetchTaskReport,
  reportBelongsToCurrentRun,
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
    ['failed', 'reproduce', 'failed', true],
    ['failed', 'env_ready', 'failed', true],
    ['failed', 'source', 'failed', false],
    ['failed', 'reproduce', 'completed', false],
    ['running', 'reproduce', 'failed', false],
  ])('canRetryFromNode(%s, %s, %s) → %s', (taskStatus, nodeKey, nodeStatus, expected) => {
    expect(canRetryFromNode(taskStatus, nodeKey, nodeStatus)).toBe(expected)
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

  it('reportBelongsToCurrentRun 拒绝上一 run 的缓存', () => {
    expect(reportBelongsToCurrentRun({ run_id: 'run-old' }, 'run-new')).toBe(false)
    expect(reportBelongsToCurrentRun({ run_id: 'run-new' }, 'run-new')).toBe(true)
    expect(reportBelongsToCurrentRun({ run_id: 'run-old' }, undefined)).toBe(false)
  })

  it('destructive confirms explain the consequence', () => {
    expect(CONFIRM_COPY.cancel.content).toMatch(/沙箱/)
    expect(CONFIRM_COPY.delete.content).toMatch(/报告/)
    expect(CONFIRM_COPY.retry.content).toMatch(/第一步|整条重跑/)
  })
})
