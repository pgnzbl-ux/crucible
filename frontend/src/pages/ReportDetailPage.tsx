import { App, Button, Card, Result, Skeleton, Space, Tabs, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, DownloadOutlined, FileTextOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useRoute } from 'wouter'

import { api } from '../shared/lib/api'
import { downloadAuthenticated, fetchAuthenticatedText } from '../shared/lib/download'
import { getReportStatusMeta, getVerdictMeta } from '../shared/lib/meta'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { ReportContent } from '../shared/components/ReportContent'
import { MarkdownBody } from '../shared/components/MarkdownBody'
import { EvidenceList } from '../features/task/components/EvidenceList'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { asRecord, documentKindOf } from '../shared/lib/reportData'

const { Text, Paragraph } = Typography

export function ReportDetailPage() {
  const [, params] = useRoute('/reports/:id')
  const [, navigate] = useLocation()
  const reportId = params?.id ?? ''
  const { message } = App.useApp()
  const qc = useQueryClient()

  const { data: report, isLoading, isError } = useQuery({
    queryKey: ['report', reportId],
    queryFn: () => api.getReport(reportId),
    enabled: !!reportId,
  })

  const { data: markdown, isLoading: mdLoading, isError: isMdError, error: mdError } = useQuery({
    queryKey: ['report-md', reportId],
    queryFn: () => fetchAuthenticatedText(api.exportReportUrl(reportId, 'md')),
    enabled: !!reportId && !!report?.report_data,
    retry: false,
  })
  useErrorToast(isMdError, mdError, '报告正文加载失败')

  const publishMutation = useMutation({
    mutationFn: () => api.publishReport(reportId),
    onSuccess: () => {
      message.success('报告已发布')
      qc.invalidateQueries({ queryKey: ['report', reportId] })
      qc.invalidateQueries({ queryKey: ['reports'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const exportFile = async (format: 'json' | 'md') => {
    try {
      await downloadAuthenticated(
        api.exportReportUrl(reportId, format),
        `report-${reportId.slice(0, 8)}.${format === 'md' ? 'md' : 'json'}`,
      )
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <>
      <PageHeader
        title={report?.title ?? '报告详情'}
        subtitle={report ? `任务 ${report.task_id.slice(0, 8)}` : undefined}
        extra={
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/reports')}>
              返回列表
            </Button>
            {report && (
              <Button onClick={() => navigate(`/tasks/${report.task_id}?tab=progress`)}>查看任务</Button>
            )}
          </Space>
        }
      />
      <PageContainer>
        {isLoading && !report ? (
          <Skeleton active paragraph={{ rows: 10 }} />
        ) : report ? (
          <Card variant="borderless">
            <Space wrap style={{ marginBottom: 16 }}>
              {report.verdict ? (
                <Tag color={getVerdictMeta(report.verdict).color}>{getVerdictMeta(report.verdict).label}</Tag>
              ) : null}
              <Tag color={getReportStatusMeta(report.status).color}>{getReportStatusMeta(report.status).label}</Tag>
              {report.severity ? <Tag>{report.severity}</Tag> : null}
              <Button size="small" icon={<DownloadOutlined />} onClick={() => exportFile('json')}>
                导出 JSON
              </Button>
              <Button
                size="small"
                icon={<FileTextOutlined />}
                onClick={() => exportFile('md')}
                disabled={!report.report_data}
              >
                {documentKindOf(asRecord(report.report_data)) === 'verification_record'
                  ? '导出验证记录'
                  : '导出 Markdown'}
              </Button>
              {report.status !== 'published' && (
                <Button
                  size="small"
                  type="primary"
                  onClick={() => publishMutation.mutate()}
                  loading={publishMutation.isPending}
                >
                  发布
                </Button>
              )}
            </Space>
            <Tabs
              defaultActiveKey="markdown"
              items={[
                {
                  key: 'markdown',
                  label: '全文',
                  children: report.report_data ? (
                    mdLoading ? (
                      <Skeleton active paragraph={{ rows: 12 }} />
                    ) : markdown ? (
                      <MarkdownBody source={markdown} />
                    ) : (
                      <Text type="secondary">无法渲染 Markdown</Text>
                    )
                  ) : report.reasoning ? (
                    <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{report.reasoning}</Paragraph>
                  ) : (
                    <Text type="secondary">报告尚无正文</Text>
                  ),
                },
                {
                  key: 'structured',
                  label: '结构化',
                  children: <ReportContent report={report} />,
                },
                {
                  key: 'evidence',
                  label: '证据',
                  children: <EvidenceList reportId={report.id} />,
                },
              ]}
            />
          </Card>
        ) : (
          <Result
            status={isError ? 'error' : '404'}
            title={isError ? '加载失败' : '报告不存在'}
            extra={
              <Button type="primary" onClick={() => navigate('/reports')}>
                返回列表
              </Button>
            }
          />
        )}
      </PageContainer>
    </>
  )
}
