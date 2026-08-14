import { Card, Collapse, Descriptions, Table, Tag, Typography, Button, Space, Empty, App } from 'antd'
import { DownloadOutlined, FileTextOutlined } from '@ant-design/icons'
import type { ReportDetail } from '../lib/api'
import { api } from '../lib/api'
import { downloadAuthenticated } from '../lib/download'
import { getVerdictMeta } from '../lib/meta'

const { Title, Paragraph, Text } = Typography

interface ReportContentProps {
  report: ReportDetail
}

/**
 * 结构化报告渲染 — 按 report_data 8 节展示(对齐 spec §4.1)。
 * report_data 为 null 时回退到 reasoning 整段。
 */
export function ReportContent({ report }: ReportContentProps) {
  const { message } = App.useApp()
  const rd = report.report_data as Record<string, unknown> | null

  const exportFile = async (format: 'json' | 'md') => {
    try {
      await downloadAuthenticated(
        api.exportReportUrl(report.id, format),
        `report-${report.id.slice(0, 8)}.${format === 'md' ? 'md' : 'json'}`,
      )
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  // 无结构化数据 → 回退
  if (!rd) {
    return (
      <Space orientation="vertical" style={{ width: '100%' }}>
        <Space>
          <Button size="small" icon={<DownloadOutlined />} onClick={() => exportFile('json')}>导出 JSON</Button>
          <Button size="small" icon={<FileTextOutlined />} onClick={() => exportFile('md')} disabled={!report.reasoning}>
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
    <Space orientation="vertical" size="medium" style={{ width: '100%' }}>
      {/* 导出按钮 */}
      <Space>
        <Button size="small" icon={<DownloadOutlined />} onClick={() => exportFile('json')}>导出 JSON</Button>
        <Button size="small" icon={<FileTextOutlined />} onClick={() => exportFile('md')}>导出 Markdown</Button>
      </Space>

      {/* 顶部判定摘要 */}
      <Card size="small">
        <Descriptions
          column={3}
          size="small"
          items={[
            {
              key: 'verdict',
              label: '判定',
              children: report.verdict ? (
                <Tag color={getVerdictMeta(report.verdict).color}>{getVerdictMeta(report.verdict).label}</Tag>
              ) : '—',
            },
            {
              key: 'cvss',
              label: 'CVSS',
              children: (
                <>
                  {report.cvss_score ?? '—'} {report.severity ? <Tag>{report.severity}</Tag> : null}
                </>
              ),
            },
            {
              key: 'file',
              label: '漏洞文件',
              children: <Text code>{report.vulnerable_file ?? String(vuln.vulnerable_file ?? '—')}</Text>,
            },
          ]}
        />
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
              <Descriptions
                column={1}
                size="small"
                bordered
                items={[
                  { key: 'type', label: '类型', children: String(vuln.type ?? '—') },
                  {
                    key: 'cvss',
                    label: 'CVSS 3.1',
                    children: `${String(cvss.vector ?? '—')} (Base ${String(cvss.base_score ?? '—')}, ${String(cvss.severity ?? '—')})`,
                  },
                  {
                    key: 'file',
                    label: '漏洞文件',
                    children: `${String(vuln.vulnerable_file ?? '—')}:${String(vuln.vulnerable_lines ?? '—')}`,
                  },
                  { key: 'pre', label: '前置条件', children: String(vuln.preconditions ?? '—') },
                  { key: 'entry', label: '触发入口', children: String(vuln.entry_point ?? '—') },
                  { key: 'harm', label: '核心危害', children: String(vuln.core_harm ?? '—') },
                  { key: 'trigger', label: '默认即触发', children: String(vuln.trigger_default ?? '—') },
                ]}
              />
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
              <Descriptions
                column={1}
                size="small"
                bordered
                items={[
                  { key: 'aff', label: '受影响版本', children: String(impact.affected_versions ?? '—') },
                  { key: 'unaff', label: '不受影响版本', children: String(impact.unaffected_versions ?? '—') },
                  {
                    key: 'trig',
                    label: '触发条件默认值',
                    children: String(impact.trigger_condition_defaults ?? '—'),
                  },
                ]}
              />
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
              <Space orientation="vertical" style={{ width: '100%' }}>
                <Descriptions
                  column={2}
                  size="small"
                  bordered
                  items={[
                    { key: 'url', label: '前端 URL', children: String(repro.frontend_url ?? '—') },
                    { key: 'product', label: '目标产品', children: String(repro.target_product ?? '—') },
                  ]}
                />
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
              <Space orientation="vertical" style={{ width: '100%' }}>
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
              <Descriptions
                column={1}
                size="small"
                bordered
                items={[
                  { key: 'rec', label: '建议', children: String(decision.recommendation ?? '—') },
                  { key: 'harm', label: '实际危害', children: String(decision.actual_harm ?? '—') },
                  { key: 'prio', label: '修复优先级', children: String(decision.fix_priority ?? '—') },
                  { key: 'reason', label: '理由', children: String(decision.reason ?? '—') },
                ]}
              />
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
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Title level={5}>4.1 代码审计分析</Title>
      {audit.length ? (
        audit.map((a, i) => (
          <Card key={i} size="small" type="inner">
            <Descriptions
              column={1}
              size="small"
              items={[
                {
                  key: 'file',
                  label: '文件',
                  children: (
                    <Text code>
                      {String(a.file ?? '—')}:{String(a.lines ?? '—')}
                    </Text>
                  ),
                },
                { key: 'flaw', label: '缺陷分析', children: String(a.flaw_explanation ?? '—') },
              ]}
            />
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
      <Descriptions
        column={1}
        size="small"
        bordered
        items={[
          { key: 'ep', label: '端点选择', children: String(pocConstr.endpoint_choice_reason ?? '—') },
          { key: 'payload', label: 'Payload 设计', children: String(pocConstr.payload_design ?? '—') },
          { key: 'chain', label: '利用链', children: String(pocConstr.exploitation_chain ?? '—') },
        ]}
      />
    </Space>
  )
}
