import { describe, expect, it } from 'vitest'

import { parseAuditOutput, splitGateReason, splitKillChain } from './auditOutput'

describe('splitGateReason', () => {
  it('把三问拆成独立段落，丢掉 Agent 自带的括号复述', () => {
    const { lead, questions } = splitGateReason(
      'Q1(核心主张): 受保护资产为宿主文件系统。Q2(链路连通): projectPath 直达 cwd。Q3(结构性阻断): 无结构性阻断。',
    )
    expect(lead).toBe('')
    expect(questions).toEqual([
      { key: 'q1', label: 'Q1 · 核心主张', text: '受保护资产为宿主文件系统。' },
      { key: 'q2', label: 'Q2 · 链路连通', text: 'projectPath 直达 cwd。' },
      { key: 'q3', label: 'Q3 · 结构性阻断', text: '无结构性阻断。' },
    ])
  })

  it('兼容 skill 正文写法：Q1 核心主张 / 运行时依赖', () => {
    const { questions } = splitGateReason(
      'Q1 核心主张：受保护资产为宿主文件系统。Q2 链路连通：projectPath 直达 cwd。Q3 结构性阻断：无阻断。运行时依赖：需登录态。',
    )
    expect(questions.map((q) => q.key)).toEqual(['q1', 'q2', 'q3', 'runtime'])
    expect(questions[0].text).toBe('受保护资产为宿主文件系统。')
    expect(questions[3].text).toBe('需登录态。')
  })

  it('兼容 Markdown 加粗的三问标记', () => {
    const { questions } = splitGateReason('**Q1 核心主张**：资产是数据库。**Q2 链路连通**：参数抵达 sink。')
    expect(questions).toHaveLength(2)
    expect(questions[0].text).toBe('资产是数据库。')
  })

  it('兼容不带括号的 Q1: 写法，并把 runtime-dependent 尾段单独成段', () => {
    const { questions } = splitGateReason(
      'Q1: 资产是数据库。Q2: 参数抵达 sink。Q3: 无阻断。runtime-dependent: 缺少登录态事实。',
    )
    expect(questions.map((q) => q.key)).toEqual(['q1', 'q2', 'q3', 'runtime'])
    expect(questions[3]).toEqual({
      key: 'runtime',
      label: '运行时依赖',
      text: '缺少登录态事实。',
    })
  })

  it('没有三问标记时整段作为 lead，不硬拆', () => {
    expect(splitGateReason('链路不通，validator 结构性阻断')).toEqual({
      lead: '链路不通，validator 结构性阻断',
      questions: [],
    })
  })

  it('三问之前的引导语保留在 lead', () => {
    const { lead, questions } = splitGateReason('先说结论：可利用。Q1: 资产是文件系统。')
    expect(lead).toBe('先说结论：可利用。')
    expect(questions).toEqual([{ key: 'q1', label: 'Q1 · 核心主张', text: '资产是文件系统。' }])
  })
})

describe('splitKillChain', () => {
  it('按箭头把超长单行切成步骤', () => {
    expect(splitKillChain('POST /api/agent -> validateApiKey(:61) → queryClaudeSDK -> Bash 执行')).toEqual([
      'POST /api/agent',
      'validateApiKey(:61)',
      'queryClaudeSDK',
      'Bash 执行',
    ])
  })

  it('换行也算步骤分隔，空段被丢掉', () => {
    expect(splitKillChain('entry\n\n-> sink\n')).toEqual(['entry', 'sink'])
  })

  it('编号列表也能切成步骤', () => {
    expect(
      splitKillChain(
        '1. POST /api/agent 2. validateApiKey 3. queryClaudeSDK 4. Bash 执行',
      ),
    ).toEqual(['POST /api/agent', 'validateApiKey', 'queryClaudeSDK', 'Bash 执行'])
  })

  it('无分隔符时返回单段，空串返回空数组', () => {
    expect(splitKillChain('一整段没有箭头的描述')).toEqual(['一整段没有箭头的描述'])
    expect(splitKillChain('   ')).toEqual([])
  })
})

describe('parseAuditOutput', () => {
  it('归一化 defense_layers / payloads 的字段别名', () => {
    const view = parseAuditOutput({
      gate_verdict: 'pass',
      gate_reason: 'Q1: 资产。Q2: 连通。Q3: 无阻断。',
      runtime_dependent: true,
      kill_chain: 'entry -> sink',
      defense_layers: [
        { layer: 'validateExternalApiKey', bypassed: '平台模式直接放行' },
        { name: 'normalizeProjectPath', bypass: '无根目录约束' },
        '裸字符串防御层',
      ],
      payloads: [
        { request: 'POST /api/agent', expectation: 'SSE 回显 /etc/shadow' },
        "' OR 1=1",
      ],
    })

    expect(view.verdict).toBe('pass')
    expect(view.runtimeDependent).toBe(true)
    expect(view.killChainSteps).toEqual(['entry', 'sink'])
    expect(view.defenseLayers).toEqual([
      { layer: 'validateExternalApiKey', bypass: '平台模式直接放行' },
      { layer: 'normalizeProjectPath', bypass: '无根目录约束' },
      { layer: '裸字符串防御层', bypass: '' },
    ])
    expect(view.payloads).toEqual([
      { request: 'POST /api/agent', expectation: 'SSE 回显 /etc/shadow' },
      { request: "' OR 1=1", expectation: '' },
    ])
    expect(view.hasStructuredDetail).toBe(true)
  })

  it('未知 gate_verdict 归到 unknown，空 output 没有可展开内容', () => {
    expect(parseAuditOutput({ gate_verdict: '瞎写的' }).verdict).toBe('unknown')
    const empty = parseAuditOutput({})
    expect(empty.verdict).toBe('unknown')
    expect(empty.hasStructuredDetail).toBe(false)
    expect(empty.questions).toEqual([])
    expect(empty.killChainSteps).toEqual([])
  })

  it('null / 非对象 output 不炸', () => {
    expect(parseAuditOutput(null).hasStructuredDetail).toBe(false)
    expect(parseAuditOutput(undefined).payloads).toEqual([])
  })

  it('只有 gate_reason 也算有内容，可展开阅读', () => {
    const view = parseAuditOutput({ gate_verdict: 'fail', gate_reason: '类型转换阻断' })
    expect(view.hasStructuredDetail).toBe(true)
    expect(view.gateReasonLead).toBe('类型转换阻断')
  })
})
