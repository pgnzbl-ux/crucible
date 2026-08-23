import { describe, expect, it } from 'vitest'

import {
  auditResultLabel,
  findingStatusLabel,
  formatFileSize,
  projectLabel,
  reportTypeLabel,
  screeningStatusMeta,
  sourceVersionLabel,
} from './tablePresentation'

describe('table presentation', () => {
  it('turns repository locators into readable project names', () => {
    expect(projectLabel('https://github.com/acme/shop.git')).toBe('acme / shop')
    expect(projectLabel('upload://local/demo-source')).toBe('demo-source')
  })

  it('describes source versions with business labels', () => {
    expect(sourceVersionLabel('release/1.2', 'branch')).toBe('分支 release/1.2')
    expect(sourceVersionLabel(null, null)).toBe('默认版本 HEAD')
  })

  it('does not treat an empty completed audit as an error', () => {
    expect(auditResultLabel('completed', null)).toBe('暂未确认漏洞')
    expect(auditResultLabel('running', null)).toBe('等待分析完成')
    expect(auditResultLabel('completed', 'confirmed')).toBe('已确认漏洞')
  })

  it('merges workflow status and resolution', () => {
    expect(findingStatusLabel('resolved', 'confirmed')).toBe('已确认漏洞')
    expect(findingStatusLabel('resolved', 'false_positive')).toBe('已排除误报')
    expect(findingStatusLabel('dispatched', null)).toBe('漏洞终认中')
  })

  it('labels report kinds and file sizes', () => {
    expect(reportTypeLabel('code_audit_report', 'discovery')).toBe('代码审计报告')
    expect(reportTypeLabel(null, 'verify')).toBe('定向验证记录')
    expect(formatFileSize(1536)).toBe('1.5 KB')
  })

  it('turns screening states into an actionable visual hierarchy', () => {
    expect(screeningStatusMeta('retained')).toEqual({ label: '重点', color: 'red' })
    expect(screeningStatusMeta('suppressed')).toEqual({ label: '已降噪', color: 'default' })
    expect(screeningStatusMeta('processing')).toEqual({ label: '初筛中', color: 'processing' })
  })
})
