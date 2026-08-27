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
  RETRYABLE_FROM_NODES,
  taskDetailTabFromValue,
} from './taskActions'
import { PIPELINE_NODE_ORDER } from './meta'

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
    ['failed', 'screen', 'failed', true],
    ['failed', 'triage', 'failed', true],
    ['failed', 'cluster', 'failed', true],
    ['failed', 'dispatch', 'failed', true],
    ['failed', 'scan_semgrep', 'failed', true],
    ['failed', 'lead_verify', 'failed', true],
    ['failed', 'finalize', 'failed', true],
    ['failed', 'source', 'failed', false],
    ['failed', 'profile', 'failed', false],
    ['failed', 'reproduce', 'completed', false],
    ['running', 'reproduce', 'failed', false],
  ])('canRetryFromNode(%s, %s, %s) → %s', (taskStatus, nodeKey, nodeStatus, expected) => {
    expect(canRetryFromNode(taskStatus, nodeKey, nodeStatus)).toBe(expected)
  })

  it('RETRYABLE_FROM_NODES 与后端派生规则锁死一致：两子图并集减 source/profile', () => {
    // 防漂移：后端 _RETRYABLE_FROM_NODES = DEFAULT_PIPELINE ∪ VERIFY_PIPELINE − {source,profile}；
    // 前端镜像若手工增删，必须同步后端，否则节点按钮显示与 API 允许范围脱节
    const expected = new Set(
      PIPELINE_NODE_ORDER.filter((key) => key !== 'source' && key !== 'profile'),
    )
    const actual = new Set<string>(RETRYABLE_FROM_NODES)
    expect(actual).toEqual(expected)
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

  it('defaults active runs to progress and terminal runs to audit overview', () => {
    expect(defaultTaskDetailTab('running')).toBe('progress')
    expect(defaultTaskDetailTab('queued')).toBe('progress')
    expect(defaultTaskDetailTab('completed')).toBe('overview')
    expect(defaultTaskDetailTab()).toBe('overview')
  })

  it('maps the legacy events tab to the merged audit process page', () => {
    expect(taskDetailTabFromValue('events')).toBe('progress')
    expect(taskDetailTabFromValue('progress')).toBe('progress')
    expect(taskDetailTabFromValue('unknown')).toBeNull()
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
