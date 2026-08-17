import { describe, expect, it } from 'vitest'

import { summarizeNodeOutput, applyNodeOverlay, displayNodeStatus, compactNodeCaption, isNodeListLoading, overlayFromSseEvents, formatAuditDetail } from './nodeOutput'

describe('summarizeNodeOutput', () => {
  it('source: MinIO 命中时写出仓库与 commit', () => {
    expect(
      summarizeNodeOutput(
        'source',
        {
          origin: 'minio',
          repo_dirname: 'claudecodeui',
          ref_name: 'main',
          commit_sha: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        },
        'completed',
      ),
    ).toBe('MinIO 缓存 · claudecodeui · ref main · @aaaaaaa')
  })

  it('profile: 只展示架构与 Web 标签，丢掉 AI 长文', () => {
    expect(
      summarizeNodeOutput(
        'profile',
        {
          language: 'nodejs',
          framework: 'express',
          is_web: true,
          port: 3001,
          detected_services: ['sqlite'],
          summary:
            'CloudCLI (aka Claude Code UI) v1.37.0 — 基于 Web 的 Claude Code / Cursor CLI 前端。React 18 + Vite 7 + Express 4。',
        },
        'completed',
      ),
    ).toBe('nodejs / express · Web · 端口 3001 · sqlite')
  })

  it('env_ready: 地址 + 登录凭据', () => {
    expect(
      summarizeNodeOutput(
        'env_ready',
        {
          target_url: 'http://192.168.1.8:3001',
          initial_creds: { username: 'admin', password: 'admin123' },
        },
        'completed',
      ),
    ).toBe('http://192.168.1.8:3001 · 账号 admin / 密码 admin123')
  })

  it('audit gate fail', () => {
    expect(
      summarizeNodeOutput('audit', { gate_verdict: 'fail', gate_reason: '链路不通' }, 'completed'),
    ).toBe('Gate 失败 · 链路不通')
  })

  it('audit gate pass', () => {
    expect(
      summarizeNodeOutput('audit', { gate_verdict: 'pass', runtime_dependent: false }, 'completed'),
    ).toBe('Gate 通过')
  })

  it('audit runtime_dependent pass', () => {
    expect(
      summarizeNodeOutput(
        'audit',
        { gate_verdict: 'pass', runtime_dependent: true, gate_reason: '需登录态' },
        'completed',
      ),
    ).toBe('Gate 通过 · 运行时依赖')
  })

  it('audit uncertain', () => {
    expect(
      summarizeNodeOutput('audit', { gate_verdict: 'uncertain', gate_reason: '对不上 sink' }, 'completed'),
    ).toBe('待复核 · 对不上 sink')
  })

  it('failed 优先用 error', () => {
    expect(summarizeNodeOutput('source', { error: '网络错误' }, 'failed')).toBe('网络错误')
  })

  it('running / skipped / cancelled', () => {
    expect(summarizeNodeOutput('env_ready', {}, 'running')).toBe('执行中')
    expect(summarizeNodeOutput('env_ready', { progress: 'Building web' }, 'running')).toBe(
      'Building web',
    )
    expect(summarizeNodeOutput('report', {}, 'skipped')).toBe('跳过')
    expect(summarizeNodeOutput('env_ready', {}, 'cancelled')).toBe('已取消')
  })

  it('cancelled task coerces in-flight nodes', () => {
    expect(displayNodeStatus('running', 'cancelled')).toBe('cancelled')
    expect(displayNodeStatus('pending', 'cancelled')).toBe('cancelled')
    expect(displayNodeStatus('completed', 'cancelled')).toBe('completed')
    expect(displayNodeStatus('running', 'running')).toBe('running')
  })

  it('SSE overlay does not revive a terminal node as running', () => {
    expect(applyNodeOverlay({ status: 'cancelled' }, { status: 'running' })).toBe('cancelled')
    expect(applyNodeOverlay({ status: 'running' }, { status: 'running' })).toBe('running')
    expect(applyNodeOverlay({ status: 'pending' }, { status: 'running' })).toBe('running')
  })
})

describe('formatAuditDetail', () => {
  it('appends kill_chain under the summary', () => {
    expect(
      formatAuditDetail(
        { gate_verdict: 'uncertain', gate_reason: '对不上', kill_chain: '只找到 /login' },
        'completed',
      ),
    ).toBe('待复核 · 对不上\n只找到 /login')
  })
})

describe('compactNodeCaption', () => {
  it('does not dump profile summary into the compact steps', () => {
    expect(
      compactNodeCaption(
        'profile',
        {
          language: 'nodejs',
          framework: 'express',
          is_web: true,
          summary: 'CloudCLI (aka Claude Code UI) v1.37.0 React 18 Vite 7 Express 4 TypeScript',
        },
        'completed',
      ),
    ).toBe('nodejs / express · Web')
  })

  it('keeps source to origin + repo', () => {
    expect(
      compactNodeCaption(
        'source',
        { origin: 'git', repo_dirname: 'claudecodeui', commit_sha: 'aaaaaaaa' },
        'completed',
      ),
    ).toBe('Git · claudecodeui')
  })

  it('running is 执行中, pending is empty', () => {
    expect(compactNodeCaption('env_ready', {}, 'running')).toBe('执行中')
    expect(compactNodeCaption('env_ready', { progress: 'Building web' }, 'running')).toBe(
      'Building web',
    )
    expect(compactNodeCaption('audit', {}, 'pending')).toBe('')
  })
})

describe('isNodeListLoading', () => {
  it('only treats an unanswered query as loading; empty array is a new run with no NodeRun yet', () => {
    expect(isNodeListLoading(undefined)).toBe(true)
    expect(isNodeListLoading([])).toBe(false)
    expect(isNodeListLoading([{ status: 'pending' }])).toBe(false)
  })
})

describe('overlayFromSseEvents', () => {
  it('paints env_ready running caption from phase.updated', () => {
    const map = overlayFromSseEvents([
      { type: 'node.updated', event: { node_key: 'env_ready', status: 'running' } },
      { type: 'phase.updated', event: { phase: 'env_ready', message: 'Building web' } },
    ])
    expect(map.get('env_ready')).toEqual({
      status: 'running',
      output: { progress: 'Building web' },
    })
  })
})

