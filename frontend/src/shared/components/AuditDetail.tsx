import type { ReactNode } from 'react'
import { Collapse, Empty, Space, Tag, Typography } from 'antd'

import { parseAuditOutput } from '../lib/auditOutput'
import { getGateVerdictMeta } from '../lib/meta'

const { Paragraph, Text } = Typography

interface AuditDetailProps {
  output: Record<string, unknown> | null | undefined
}

/**
 * 白盒审计结论的分区排版。
 *
 * Agent 交回的是几百字压成一行的 gate_reason 与 kill_chain，直接 pre-wrap 没法读。
 * 这里按「Gate 结论 → 三问 → 利用链 → 防御层 → Payload」分区，长正文折叠或限行展开。
 */
export function AuditDetail({ output }: AuditDetailProps) {
  const view = parseAuditOutput(output)
  const gate = getGateVerdictMeta(view.verdict)

  const panels = [
    view.killChainSteps.length > 0 && {
      key: 'chain',
      label: `利用链${view.killChainSteps.length > 1 ? ` · ${view.killChainSteps.length} 步` : ''}`,
      children:
        view.killChainSteps.length > 1 ? (
          <ol className="crucible-audit__chain">
            {view.killChainSteps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        ) : (
          <Paragraph
            className="crucible-audit__code"
            style={{ marginBottom: 0 }}
            ellipsis={{ rows: 4, expandable: true, symbol: '展开' }}
          >
            {view.killChainSteps[0]}
          </Paragraph>
        ),
    },
    view.defenseLayers.length > 0 && {
      key: 'defense',
      label: `防御层 · ${view.defenseLayers.length} 层`,
      children: (
        <div className="crucible-audit__layers">
          {view.defenseLayers.map((layer, i) => (
            <div key={i} className="crucible-audit__layer">
              <Text strong>{layer.layer || `第 ${i + 1} 层`}</Text>
              {layer.bypass ? (
                <Text type="secondary">绕过：{layer.bypass}</Text>
              ) : (
                <Text type="secondary">未说明是否可绕过</Text>
              )}
            </div>
          ))}
        </div>
      ),
    },
    view.payloads.length > 0 && {
      key: 'payloads',
      label: `Payload · ${view.payloads.length} 条`,
      children: (
        <div className="crucible-audit__layers">
          {view.payloads.map((payload, i) => (
            <div key={i} className="crucible-audit__payload">
              <Paragraph
                className="crucible-audit__code"
                copyable={{ text: payload.request }}
                style={{ marginBottom: payload.expectation ? 6 : 0 }}
              >
                {payload.request || '（未给出请求）'}
              </Paragraph>
              {payload.expectation ? (
                <Text type="secondary">预期：{payload.expectation}</Text>
              ) : null}
            </div>
          ))}
        </div>
      ),
    },
  ].filter(Boolean) as { key: string; label: string; children: ReactNode }[]

  return (
    <div className="crucible-audit">
      <Space size={[6, 6]} wrap>
        <Tag color={gate.color}>{gate.label}</Tag>
        {view.runtimeDependent ? <Tag color="gold">运行时依赖</Tag> : null}
      </Space>

      {view.questions.length > 0 ? (
        <div className="crucible-audit__questions">
          {view.gateReasonLead ? (
            <Paragraph type="secondary" style={{ marginBottom: 8 }}>
              {view.gateReasonLead}
            </Paragraph>
          ) : null}
          {view.questions.map((q, i) => (
            <div key={`${q.key}-${i}`} className="crucible-audit__question">
              <Text strong className="crucible-audit__question-label">
                {q.label}
              </Text>
              <Paragraph
                style={{ marginBottom: 0 }}
                ellipsis={{ rows: 3, expandable: true, symbol: '展开' }}
              >
                {q.text || '—'}
              </Paragraph>
            </div>
          ))}
        </div>
      ) : view.gateReasonLead ? (
        <Paragraph
          className="crucible-audit__questions"
          style={{ marginBottom: 0 }}
          ellipsis={{ rows: 4, expandable: true, symbol: '展开' }}
        >
          {view.gateReasonLead}
        </Paragraph>
      ) : null}

      {panels.length > 0 ? (
        <Collapse
          ghost
          size="small"
          className="crucible-audit__panels"
          defaultActiveKey={[]}
          items={panels}
        />
      ) : null}

      {!view.hasStructuredDetail ? (
        <Empty
          description="Agent 未交回审计明细"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ margin: '8px 0' }}
        />
      ) : null}
    </div>
  )
}
