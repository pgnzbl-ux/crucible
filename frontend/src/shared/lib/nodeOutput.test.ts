import { describe, expect, it } from 'vitest'

import { summarizeNodeOutput, applyNodeOverlay, displayNodeStatus, compactNodeCaption, isNodeListLoading, overlayFromSseEvents, parseInitialCreds, nodeStepsPollMs } from './nodeOutput'

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

  it('source: 本地上传写出仓库与内容指纹', () => {
    expect(
      summarizeNodeOutput(
        'source',
        {
          origin: 'upload',
          repo_dirname: 'demo',
          ref_name: 'local',
          commit_sha: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
        },
        'completed',
      ),
    ).toBe('本地上传 · demo · ref local · @bbbbbbb')
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

  it('env_ready: 地址 + 已提供凭据，摘要不带密码', () => {
    expect(
      summarizeNodeOutput(
        'env_ready',
        {
          target_url: 'http://192.168.1.8:3001',
          initial_creds: { username: 'admin', password: 'admin123' },
        },
        'completed',
      ),
    ).toBe('http://192.168.1.8:3001 · 已提供凭据 · admin')
    expect(
      summarizeNodeOutput(
        'env_ready',
        {
          target_url: 'http://192.168.1.8:3001',
          initial_creds: { username: 'admin', password: 'admin123' },
        },
        'completed',
      ),
    ).not.toContain('admin123')
  })

  it('audit gate fail 只留短标签，长 gate_reason 留给详情区', () => {
    const longReason = 'Q1 核心主张：'.padEnd(120, '长')
    expect(
      summarizeNodeOutput('audit', { gate_verdict: 'fail', gate_reason: longReason }, 'completed'),
    ).toBe('Gate 失败（误报）')
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

  it('audit uncertain 只留短标签', () => {
    expect(
      summarizeNodeOutput('audit', { gate_verdict: 'uncertain', gate_reason: '对不上 sink' }, 'completed'),
    ).toBe('待复核')
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

  it('failed task only coerces the active node; downstream pending nodes stay unexecuted', () => {
    expect(displayNodeStatus('running', 'failed')).toBe('failed')
    expect(displayNodeStatus('pending', 'failed')).toBe('pending')
    expect(displayNodeStatus('completed', 'failed')).toBe('completed')
  })

  it('SSE overlay does not revive a terminal node as running', () => {
    expect(applyNodeOverlay({ status: 'cancelled' }, { status: 'running' })).toBe('cancelled')
    expect(applyNodeOverlay({ status: 'running' }, { status: 'running' })).toBe('running')
    expect(applyNodeOverlay({ status: 'pending' }, { status: 'running' })).toBe('running')
  })
})

describe('parseInitialCreds', () => {
  it('有账号密码时给出 creds 态', () => {
    expect(parseInitialCreds({ username: 'admin', password: 'admin123', login_url: '/login' })).toEqual({
      state: 'creds',
      username: 'admin',
      password: 'admin123',
      loginUrl: '/login',
      note: '',
    })
  })

  it('兼容 user / email / pass 别名', () => {
    const view = parseInitialCreds({ email: 'root@a.com', pass: 'x' })
    expect(view.state).toBe('creds')
    expect(view.username).toBe('root@a.com')
    expect(view.password).toBe('x')
  })

  it('Agent 明确 auth_required=false 时是免登录，不是缺数据', () => {
    expect(parseInitialCreds({ auth_required: false, note: '平台模式跳过认证' })).toMatchObject({
      state: 'no_auth',
      note: '平台模式跳过认证',
    })
  })

  it('空对象 / null 是未知态，不冒充免登录', () => {
    expect(parseInitialCreds({}).state).toBe('unknown')
    expect(parseInitialCreds(null).state).toBe('unknown')
    expect(parseInitialCreds('admin/admin').state).toBe('unknown')
  })

  it('只给 note 时保留说明但仍是未知态', () => {
    expect(parseInitialCreds({ note: '需自行注册' })).toMatchObject({
      state: 'unknown',
      note: '需自行注册',
    })
  })
})

describe('env_ready 凭据摘要', () => {
  it('免登录写成免登录，而不是留白', () => {
    expect(
      summarizeNodeOutput(
        'env_ready',
        { target_url: 'http://127.0.0.1:3002', initial_creds: { auth_required: false } },
        'completed',
      ),
    ).toBe('http://127.0.0.1:3002 · 免登录')
  })

  it('凭据缺失时点明未提供，避免误以为界面丢了数据', () => {
    expect(
      summarizeNodeOutput('env_ready', { target_url: 'http://127.0.0.1:3002', initial_creds: {} }, 'completed'),
    ).toBe('http://127.0.0.1:3002 · 无预设凭据')
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

  it('audit compact 不截断长 gate_reason', () => {
    const longReason = 'Q1 核心主张：'.padEnd(80, '长')
    expect(
      compactNodeCaption('audit', { gate_verdict: 'fail', gate_reason: longReason }, 'completed'),
    ).toBe('Gate 失败')
  })

  it('running is 执行中, pending is empty', () => {
    expect(compactNodeCaption('env_ready', {}, 'running')).toBe('执行中')
    expect(compactNodeCaption('env_ready', { progress: 'Building web' }, 'running')).toBe(
      'Building web',
    )
    expect(compactNodeCaption('audit', {}, 'pending')).toBe('')
  })
})

describe('nodeStepsPollMs', () => {
  const sixDone = Array.from({ length: 6 }, () => ({ status: 'completed' }))

  it('stops when all six nodes are terminal', () => {
    expect(nodeStepsPollMs({ nodes: sixDone })).toBe(false)
  })

  it('stops while SSE is live', () => {
    expect(nodeStepsPollMs({ nodes: [{ status: 'running' }], sseLive: true })).toBe(false)
  })

  it('polls 3s when SSE is down and work remains', () => {
    expect(nodeStepsPollMs({ nodes: [{ status: 'running' }], sseLive: false })).toBe(3000)
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

  it('paints profile running caption from phase.updated', () => {
    const map = overlayFromSseEvents([
      { type: 'node.updated', event: { node_key: 'profile', status: 'running' } },
      { type: 'phase.updated', event: { phase: 'profile', message: '规则扫描完成（python/fastapi · 1 语言）' } },
      { type: 'phase.updated', event: { phase: 'profile', message: '启动轻度 AI 画像' } },
    ])
    expect(map.get('profile')).toEqual({
      status: 'running',
      output: { progress: '启动轻度 AI 画像' },
    })
  })

  it('paints scan_semgrep running caption from phase.updated', () => {
    const map = overlayFromSseEvents([
      { type: 'node.updated', event: { node_key: 'scan_semgrep', status: 'running' } },
      { type: 'phase.updated', event: { phase: 'scan_semgrep', message: '规则包 python · 超时上限 1200s' } },
      { type: 'phase.updated', event: { phase: 'scan_semgrep', message: '扫描进行中…已 45s / 上限 1200s' } },
    ])
    expect(map.get('scan_semgrep')).toEqual({
      status: 'running',
      output: { progress: '扫描进行中…已 45s / 上限 1200s' },
    })
  })

  it('paints cluster running caption from phase.updated', () => {
    const map = overlayFromSseEvents([
      { type: 'node.updated', event: { node_key: 'cluster', status: 'running' } },
      { type: 'phase.updated', event: { phase: 'cluster', message: '构建函数索引（语言 python）' } },
      { type: 'phase.updated', event: { phase: 'cluster', message: '分组完成：3 组（A=1 B=1 F=0 bypass=1）' } },
    ])
    expect(map.get('cluster')).toEqual({
      status: 'running',
      output: { progress: '分组完成：3 组（A=1 B=1 F=0 bypass=1）' },
    })
  })

  it('paints triage running caption from phase.updated', () => {
    const map = overlayFromSseEvents([
      { type: 'node.updated', event: { node_key: 'triage', status: 'running' } },
      { type: 'phase.updated', event: { phase: 'triage', message: '待审 12 组（跳过 LLM 3）' } },
      { type: 'phase.updated', event: { phase: 'triage', message: '二审 2/12：CWE-89 app/db.py' } },
    ])
    expect(map.get('triage')).toEqual({
      status: 'running',
      output: { progress: '二审 2/12：CWE-89 app/db.py' },
    })
  })

  it('ignores concurrent triage start phases and prefers triage.progress caption', () => {
    const map = overlayFromSseEvents([
      { type: 'node.updated', event: { node_key: 'triage', status: 'running' } },
      {
        type: 'phase.updated',
        event: { phase: 'triage', message: '开始审议：? www/static/js/a.js（族内 5 组）' },
      },
      {
        type: 'triage.progress',
        event: {
          node_key: 'triage',
          done: 3,
          total: 12,
          label: 'CWE-89 app/db.py',
          family_size: 2,
          adjudicated: 3,
          pending: 9,
          message: '二审 3/12：CWE-89 app/db.py（族内 2 组）',
        },
      },
    ])
    expect(map.get('triage')).toEqual({
      status: 'running',
      output: { progress: '二审 3/12：CWE-89 app/db.py（族内 2 组）' },
    })
  })

  it('does not paint screen fast-review triage.progress onto AI triage', () => {
    const map = overlayFromSseEvents([
      { type: 'node.updated', event: { node_key: 'triage', status: 'running' } },
      {
        type: 'triage.progress',
        event: {
          node_key: 'screen',
          stage: 'fast_screen',
          adjudicated: 10,
          pending: 3,
          message: '快审不应出现在二审',
        },
      },
    ])
    expect(map.get('triage')).toEqual({ status: 'running' })
  })

  it('summarizes completed triage from output counts', () => {
    expect(
      summarizeNodeOutput(
        'triage',
        { adjudicated_count: 40, family_count: 12 },
        'completed',
      ),
    ).toBe('已审 40 · 12 族')
  })

  it('stacks usage.updated cumulative onto the node', () => {
    const map = overlayFromSseEvents([
      {
        type: 'usage.updated',
        event: {
          node_key: 'triage',
          usage: {
            prompt_tokens: 10,
            completion_tokens: 2,
            cache_read_input_tokens: 100,
            cache_creation_input_tokens: 0,
            total_tokens: 112,
          },
          cumulative: {
            prompt_tokens: 30,
            completion_tokens: 6,
            cache_read_input_tokens: 500,
            cache_creation_input_tokens: 0,
            total_tokens: 536,
          },
        },
      },
    ])
    expect(map.get('triage')?.usage).toEqual({
      prompt_tokens: 30,
      completion_tokens: 6,
      cache_read_input_tokens: 500,
      cache_creation_input_tokens: 0,
      total_tokens: 536,
    })
  })
})
