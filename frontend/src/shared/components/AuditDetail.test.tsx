import { App } from 'antd'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { AuditDetail } from './AuditDetail'

function render(output: Record<string, unknown> | null): string {
  return renderToStaticMarkup(
    <App>
      <AuditDetail output={output} />
    </App>,
  )
}

describe('AuditDetail', () => {
  it('把 Gate 结论、三问与各分区计数摆到可见处', () => {
    const html = render({
      gate_verdict: 'pass',
      gate_reason: 'Q1: 资产是宿主文件系统。Q2: projectPath 直达 cwd。Q3: 无结构性阻断。',
      runtime_dependent: true,
      kill_chain: 'POST /api/agent -> normalizeProjectPath -> Bash 执行',
      defense_layers: [
        { layer: 'validateExternalApiKey', bypassed: '平台模式放行' },
        { name: 'normalizeProjectPath', bypass: '无根目录约束' },
      ],
      payloads: [{ request: 'POST /api/agent', expectation: '回显 /etc/shadow' }],
    })

    expect(html).toContain('Gate 通过')
    expect(html).toContain('运行时依赖')
    expect(html).toContain('Q1 · 核心主张')
    expect(html).toContain('Q3 · 结构性阻断')
    expect(html).toContain('资产是宿主文件系统。')
    expect(html).toContain('利用链 · 3 步')
    expect(html).toContain('防御层 · 2 层')
    expect(html).toContain('Payload · 1 条')
  })

  it('误报结论走绿色语义，没有三问时展示原文', () => {
    const html = render({ gate_verdict: 'fail', gate_reason: '类型转换让 payload 不可能成立' })
    expect(html).toContain('Gate 失败（误报）')
    expect(html).toContain('类型转换让 payload 不可能成立')
  })

  it('Agent 没交明细时明说，而不是留一片空白', () => {
    expect(render({})).toContain('Agent 未交回审计明细')
    expect(render(null)).toContain('Agent 未交回审计明细')
  })
})
