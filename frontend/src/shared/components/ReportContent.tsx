import { Card, Collapse, Descriptions, Table, Tag, Typography, Button, Space, Empty } from 'antd'
import { DownloadOutlined, FileTextOutlined } from '@ant-design/icons'
import type { ReportDetail } from '../lib/api'
import { getVerdictMeta } from '../lib/meta'

const { Title, Paragraph, Text, Link } = Typography

interface ReportContentProps {
  report: ReportDetail
}

/**
 * 结构化报告渲染 — 按 report_data 8 节展示(对齐 spec §4.1)。
 * report_data 为 null 时回退到 reasoning 整段。
 */
export function ReportContent({ report }: ReportContentProps) {
  const rd = report.report_data as Record<string, unknown> | null

  const exportJson = () => {
    window.open(`/api/v1/reports/${report.id}/export?format=json`, '_blank')
  }
  const exportMd = () => {
    window.open(`/api/v1/reports/${report.id}/export?format=md`, '_blank')
  }

  // 无结构化数据 → 回退
  if (!rd) {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Space>
          <Button size="small" icon={<DownloadOutlined />} onClick={exportJson}>导出 JSON</Button>
          <Button size="small" icon={<FileTextOutlined />} onClick={exportMd} disabled={!report.reasoning}>
            导出 Markdown
          </Button>
        </Space>
        {report.reasoning ? (
          <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{report.reasoning}</Paragraph>
        ) : (
          <Empty description="报告尚无结构化数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Space>
    )
  }

  const vuln = (rd.vulnerability as Record<string, unknown>) || {}
  const cvss = (vuln.cvss as Record<string, unknown>) || {}
  const impact = (rd.impact as Record<string, unknown>) || {}
  const details = (rd.details as Record<string, unknown>) || {}
  const repro = (rd.reproduction as Record<string, unknown>) || {}
  const decision = (rd.reporting_decision as Record<string, unknown>) || {}
  const fixes = (rd.fix_suggestions as Array<Record<string, unknown>>) || []
  const pocCmds = (rd.poc_commands as string[]) || []
  const steps = (repro.steps as Array<Record<string, unknown>>) || []

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 导出按钮 */}
      <Space>
        <Button size="small" icon={<DownloadOutlined />} onClick={exportJson}>导出 JSON</Button>
        <Button size="small" icon={<FileTextOutlined />} onClick={exportMd}>导出 Markdown</Button>
      </Space>

      {/* 顶部判定摘要 */}
      <Card size="small">
        <Descriptions column={3} size="small">
          <Descriptions.Item label="判定">
            {report.verdict ? (
              <Tag color={getVerdictMeta(report.verdict).color}>{getVerdictMeta(report.verdict).label}</Tag>
            ) : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="CVSS">
            {report.cvss_score ?? '—'} {report.severity ? <Tag>{report.severity}</Tag> : null}
          </Descriptions.Item>
          <Descriptions.Item label="漏洞文件">
            <Text code>{report.vulnerable_file ?? String(vuln.vulnerable_file ?? '—')}</Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* §1 产品介绍 */}
      <Collapse
        defaultActiveKey={['1']}
        items={[
          {
            key: '1',
            label: '§1 产品介绍',
            children: <Paragraph>{String(rd.product_intro ?? '—')}</Paragraph>,
          },
        ]}
      />

      {/* §2 漏洞描述 */}
      <Collapse
        items={[
          {
            key: '2',
            label: '§2 漏洞描述',
            children: (
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="类型">{String(vuln.type ?? '—')}</Descriptions.Item>
                <Descriptions.Item label="CVSS 3.1">
                  {String(cvss.vector ?? '—')} (Base {String(cvss.base_score ?? '—')}, {String(cvss.severity ?? '—')})
                </Descriptions.Item>
                <Descriptions.Item label="漏洞文件">
                  {String(vuln.vulnerable_file ?? '—')}:{String(vuln.vulnerable_lines ?? '—')}
                </Descriptions.Item>
                <Descriptions.Item label="前置条件">{String(vuln.preconditions ?? '—')}</Descriptions.Item>
                <Descriptions.Item label="触发入口">{String(vuln.entry_point ?? '—')}</Descriptions.Item>
                <Descriptions.Item label="核心危害">{String(vuln.core_harm ?? '—')}</Descriptions.Item>
                <Descriptions.Item label="默认即触发">{String(vuln.trigger_default ?? '—')}</Descriptions.Item>
              </Descriptions>
            ),
          },
        ]}
      />

      {/* §3 影响范围 */}
      <Collapse
        items={[
          {
            key: '3',
            label: '§3 影响范围',
            children: (
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="受影响版本">{String(impact.affected_versions ?? '—')}</Descriptions.Item>
                <Descriptions.Item label="不受影响版本">{String(impact.unaffected_versions ?? '—')}</Descriptions.Item>
                <Descriptions.Item label="触发条件默认值">{String(impact.trigger_condition_defaults ?? '—')}</Descriptions.Item>
              </Descriptions>
            ),
          },
        ]}
      />

      {/* §4 漏洞详情 */}
      <Collapse
        items={[
          {
            key: '4',
            label: '§4 漏洞详情',
            children: <ReportDetails details={details} />,
          },
        ]}
      />

      {/* §5 漏洞复现 */}
      <Collapse
        items={[
          {
            key: '5',
            label: '§5 漏洞复现',
            children: (
              <Space direction="vertical" style={{ width: '100%' }}>
                <Descriptions column={2} size="small" bordered>
                  <Descriptions.Item label="前端 URL">{String(repro.frontend_url ?? '—')}</Descriptions.Item>
                  <Descriptions.Item label="目标产品">{String(repro.target_product ?? '—')}</Descriptions.Item>
                </Descriptions>
                {steps.length > 0 && (
                  <Table
                    size="small"
                    rowKey={(r) => String(r.step)}
                    dataSource={steps}
                    pagination={false}
                    columns={[
                      { title: '步骤', dataIndex: 'step', width: 60 },
                      { title: '动作', dataIndex: 'action' },
                      { title: '观察', dataIndex: 'observation' },
                      {
                        title: '截图',
                        dataIndex: 'screenshot',
                        render: (v: string) => (v ? <Text code>{v}</Text> : '—'),
                      },
                    ]}
                  />
                )}
                <Paragraph type="secondary">攻击链: {String(repro.attack_chain_diagram ?? '—')}</Paragraph>
              </Space>
            ),
          },
        ]}
      />

      {/* §6 POC */}
      <Collapse
        items={[
          {
            key: '6',
            label: '§6 POC',
            children: pocCmds.length ? (
              <Space direction="vertical" style={{ width: '100%' }}>
                {pocCmds.map((c, i) => (
                  <Text key={i} code copyable style={{ whiteSpace: 'pre-wrap', display: 'block' }}>
                    {c}
                  </Text>
                ))}
              </Space>
            ) : (
              <Text type="secondary">—</Text>
            ),
          },
        ]}
      />

      {/* §7 修复建议 */}
      <Collapse
        items={[
          {
            key: '7',
            label: '§7 修复建议',
            children: fixes.length ? (
              <Table
                size="small"
                rowKey={(r) => String(r.priority)}
                dataSource={fixes}
                pagination={false}
                columns={[
                  { title: '优先级', dataIndex: 'priority', width: 80 },
                  { title: '建议', dataIndex: 'suggestion' },
                ]}
              />
            ) : (
              <Text type="secondary">—</Text>
            ),
          },
        ]}
      />

      {/* §8 报送判定 */}
      <Collapse
        items={[
          {
            key: '8',
            label: '§8 报送判定',
            children: (
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="建议">{String(decision.recommendation ?? '—')}</Descriptions.Item>
                <Descriptions.Item label="实际危害">{String(decision.actual_harm ?? '—')}</Descriptions.Item>
                <Descriptions.Item label="修复优先级">{String(decision.fix_priority ?? '—')}</Descriptions.Item>
                <Descriptions.Item label="理由">{String(decision.reason ?? '—')}</Descriptions.Item>
              </Descriptions>
            ),
          },
        ]}
      />
    </Space>
  )
}

function ReportDetails({ details }: { details: Record<string, unknown> }) {
  const audit = (details.audit_analysis as Array<Record<string, unknown>>) || []
  const pocConstr = (details.poc_construction as Record<string, unknown>) || {}
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Title level={5}>4.1 代码审计分析</Title>
      {audit.length ? (
        audit.map((a, i) => (
          <Card key={i} size="small" type="inner">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="文件">
                <Text code>{String(a.file ?? '—')}:{String(a.lines ?? '—')}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="缺陷分析">{String(a.flaw_explanation ?? '—')}</Descriptions.Item>
            </Descriptions>
            {a.content ? (
              <Paragraph code copyable style={{ whiteSpace: 'pre-wrap', marginTop: 8 }}>
                {String(a.content)}
              </Paragraph>
            ) : null}
          </Card>
        ))
      ) : (
        <Text type="secondary">—</Text>
      )}
      <Title level={5}>4.2 PoC 构造思路</Title>
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="端点选择">{String(pocConstr.endpoint_choice_reason ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="Payload 设计">{String(pocConstr.payload_design ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="利用链">{String(pocConstr.exploitation_chain ?? '—')}</Descriptions.Item>
      </Descriptions>
    </Space>
  )
}
