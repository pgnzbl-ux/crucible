import { Card, Collapse, Descriptions, Tag, Typography, Button, Space, Empty, App } from 'antd'
import { DownloadOutlined, FileTextOutlined } from '@ant-design/icons'
import type { ReportDetail } from '../lib/api'
import { api } from '../lib/api'
import { downloadAuthenticated } from '../lib/download'
import { getVerdictMeta } from '../lib/meta'
import { REPORT_SECTIONS, asMarkdownSection, asRecord } from '../lib/reportData'
import { MarkdownBody } from './MarkdownBody'

const { Paragraph, Text } = Typography

interface ReportContentProps {
  report: ReportDetail
}

/**
 * 8 节 Markdown 报告。顶栏只读索引列；正文走 MarkdownBody。
 * report_data 为 null 时回退到 reasoning 整段。
 */
export function ReportContent({ report }: ReportContentProps) {
  const { message } = App.useApp()
  const rd = report.report_data ? asRecord(report.report_data) : null

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
      </Space>
    )
  }

  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Space>
        <Button size="small" icon={<DownloadOutlined />} onClick={() => exportFile('json')}>导出 JSON</Button>
        <Button size="small" icon={<FileTextOutlined />} onClick={() => exportFile('md')}>导出 Markdown</Button>
      </Space>

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
              children: <Text code>{report.vulnerable_file ?? '—'}</Text>,
            },
          ]}
        />
      </Card>

      <Collapse
        defaultActiveKey={['product_intro']}
        items={REPORT_SECTIONS.map((section) => {
          const md = asMarkdownSection(rd[section.key])
          return {
            key: section.key,
            label: section.label,
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
