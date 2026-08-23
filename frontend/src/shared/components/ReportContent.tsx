import { Alert, Card, Collapse, Descriptions, Tag, Typography, Button, Space, Empty, App } from 'antd'
import { DownloadOutlined, FileTextOutlined } from '@ant-design/icons'
import type { ReportDetail } from '../lib/api'
import { api } from '../lib/api'
import { downloadAuthenticated } from '../lib/download'
import { getVerdictMeta } from '../lib/meta'
import { asMarkdownSection, asRecord, documentKindOf, formatDenoiseFunnel, pocToMarkdown, sectionsFor } from '../lib/reportData'
import { AuditPanel } from './AuditPanel'
import { MarkdownBody } from './MarkdownBody'

const { Paragraph, Text } = Typography

interface ReportContentProps {
  report: ReportDetail
}

/**
 * 按 document_kind 渲染漏洞报告或验证记录。
 * report_data 为 null 时回退到 reasoning 整段。
 */
export function ReportContent({ report }: ReportContentProps) {
  const { message } = App.useApp()
  const rd = report.report_data ? asRecord(report.report_data) : null
  const kind = documentKindOf(rd)
  const isRecord = kind === 'verification_record'
  const sections = sectionsFor(rd)
  const mdLabel = isRecord ? '导出验证记录' : '导出 Markdown'
  const denoiseLine = formatDenoiseFunnel(rd)

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
        <AuditPanel taskId={report.task_id} runId={report.run_id} />
      </Space>
    )
  }

  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Space>
        <Button size="small" icon={<DownloadOutlined />} onClick={() => exportFile('json')}>导出 JSON</Button>
        <Button size="small" icon={<FileTextOutlined />} onClick={() => exportFile('md')}>{mdLabel}</Button>
      </Space>

      {isRecord ? (
        <Alert
          type="info"
          showIcon
          title="未形成漏洞 PoC/CVSS"
          description="本结果是验证记录：保留白盒结论、失败请求与阻断原因，不生成漏洞 PoC，不评定 CVSS。"
        />
      ) : null}

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
              children: isRecord ? (
                '未评定'
              ) : (
                <>
                  {report.cvss_score ?? '—'} {report.severity ? <Tag>{report.severity}</Tag> : null}
                </>
              ),
            },
            {
              key: 'file',
              label: '漏洞文件',
              children: <Text code>{report.vulnerable_file ?? '—'}</Text>,
            },
            ...(denoiseLine
              ? [{
                  key: 'denoise',
                  label: '降噪漏斗',
                  children: <Text type="secondary">{denoiseLine}</Text>,
                  span: 3 as const,
                }]
              : []),
          ]}
        />
      </Card>

      <AuditPanel taskId={report.task_id} runId={report.run_id} />

      <Collapse
        defaultActiveKey={[sections[0].key]}
        items={sections.map((section) => {
          const isPoc = section.key === 'poc_commands'
          const pocMd = isPoc
            ? pocToMarkdown(report.poc_language, report.poc_code, report.poc_usage)
            : null
          const md = pocMd ?? asMarkdownSection(rd[section.key])
          return {
            key: section.key,
            label: isPoc && report.poc_filename
              ? `${section.label} · ${report.poc_filename}`
              : section.label,
            forceRender: Boolean(pocMd),
            children: md ? (
              <MarkdownBody source={md} />
            ) : (
              <Text type="secondary">报告格式已升级，请重新跑任务</Text>
            ),
          }
        })}
      />
    </Space>
  )
}
