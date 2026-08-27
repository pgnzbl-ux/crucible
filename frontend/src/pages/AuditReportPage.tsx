import { App, Button, Card, Descriptions, Empty, Space, Table, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, DownloadOutlined, EyeOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation, useRoute } from 'wouter'

import { api, type VulnReportSummary } from '../shared/lib/api'
import { downloadAuthenticated } from '../shared/lib/download'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { tableRowNavigateProps } from '../shared/lib/tableRowNavigate'
import { projectLabel, sourceVersionLabel } from '../shared/lib/tablePresentation'
import { getReportStatusMeta, getVerdictMeta } from '../shared/lib/meta'

import { MarkdownBody } from '../shared/components/MarkdownBody'

const { Paragraph, Text, Title } = Typography

function basisLabel(basis: string | null | undefined) {
  if (basis === 'lab') return '靶场验证'
  if (basis === 'code_path') return '代码闭环'
  return basis || '—'
}

export function AuditReportPage() {
  const [, params] = useRoute('/reports/audits/:taskId')
  const [, navigate] = useLocation()
  const taskId = params?.taskId || ''

  const taskQuery = useQuery({
    queryKey: ['audit-task', taskId],
    queryFn: () => api.getAuditTask(taskId),
    enabled: Boolean(taskId),
  })
  const vulnsQuery = useQuery({
    queryKey: ['audit-vulns', taskId],
    queryFn: () => api.listAuditVulnReports(taskId),
    enabled: Boolean(taskId),
  })
  useErrorToast(taskQuery.isError, taskQuery.error, '审计任务加载失败')
  useErrorToast(vulnsQuery.isError, vulnsQuery.error, '漏洞报告列表加载失败')

  const task = taskQuery.data
  const columns: ColumnsType<VulnReportSummary> = [
    {
      title: '简述',
      dataIndex: 'summary',
      ellipsis: true,
      render: (v: string) => <Text strong>{v}</Text>,
    },
    {
      title: '终认',
      dataIndex: 'final_verdict',
      width: 120,
      render: (v: string | null) =>
        v ? <Tag color={getVerdictMeta(v).color}>{getVerdictMeta(v).label}</Tag> : '—',
    },
    {
      title: '验证方式',
      dataIndex: 'verification_basis',
      width: 110,
      render: (v: string | null) => (
        <Tag color={v === 'lab' ? 'blue' : 'gold'}>{basisLabel(v)}</Tag>
      ),
    },
    {
      title: '引擎',
      dataIndex: 'primary_engine',
      width: 100,
      render: (v: string | null) => v || '—',
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_: unknown, row) => (
        <Button
          size="small"
          type="primary"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/reports/audits/${taskId}/vulns/${row.alert_group_id}`)}
        >
          阅读
        </Button>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title={task ? projectLabel(task.project_address) : '审计报告'}
        subtitle={
          task
            ? `${sourceVersionLabel(task.project_ref, null)} · 确认 ${task.confirmed_count} · 可达 ${task.code_reachable_count}`
            : '加载中…'
        }
        extra={
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/reports?tab=audit')}>
            返回列表
          </Button>
        }
      />
      <PageContainer>
        {task && (
          <Card size="small" style={{ marginBottom: 16 }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="任务 ID">
                <Text code>{task.task_id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="任务状态">
                <Tag color={task.task_status === 'completed' ? 'green' : 'blue'}>{task.task_status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {task.created_at ? dayjs(task.created_at).format('YYYY-MM-DD HH:mm') : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="发布状态">
                {task.report_status
                  ? (() => {
                      const m = getReportStatusMeta(task.report_status)
                      return <Tag color={m.color}>{m.label}</Tag>
                    })()
                  : '—'}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        )}
        <Title level={5} style={{ margin: '16px 0 8px' }}>单漏洞终认报告清单</Title>
        <Table<VulnReportSummary>
          rowKey="alert_group_id"
          loading={vulnsQuery.isLoading || vulnsQuery.isFetching}
          columns={columns}
          dataSource={vulnsQuery.data?.items ?? []}
          pagination={false}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="该任务尚无终认成功的漏洞报告"
              />
            ),
          }}
          onRow={(row) =>
            tableRowNavigateProps(() =>
              navigate(`/reports/audits/${taskId}/vulns/${row.alert_group_id}`),
            )
          }
        />
      </PageContainer>
    </>
  )
}

export function AuditVulnReportPage() {
  const [, params] = useRoute('/reports/audits/:taskId/vulns/:groupId')
  const [, navigate] = useLocation()
  const { message } = App.useApp()
  const taskId = params?.taskId || ''
  const groupId = params?.groupId || ''

  const { data, error, isError, isLoading } = useQuery({
    queryKey: ['audit-vuln', taskId, groupId],
    queryFn: () => api.getAuditVulnReport(taskId, groupId),
    enabled: Boolean(taskId && groupId),
  })
  useErrorToast(isError, error, '漏洞报告加载失败')

  const summary = typeof data?.summary === 'string' ? data.summary : '漏洞报告'
  const reasoning = typeof data?.reasoning === 'string' ? data.reasoning : ''
  const remediation = typeof data?.remediation === 'string' ? data.remediation : ''
  const basis = typeof data?.verification_basis === 'string' ? data.verification_basis : null
  const finalVerdict = typeof data?.final_verdict === 'string' ? data.final_verdict : null
  const engines = Array.isArray(data?.engines) ? data.engines.map(String).join(', ') : '—'
  const locus = data?.locus && typeof data.locus === 'object' ? (data.locus as Record<string, unknown>) : {}

  const exportFile = async (format: 'json' | 'md') => {
    try {
      await downloadAuthenticated(
        api.exportAuditVulnUrl(taskId, groupId, format),
        `vuln-${groupId.slice(0, 8)}.${format === 'md' ? 'md' : 'json'}`,
      )
    } catch (e) {
      message.error(e instanceof Error ? e.message : '导出失败')
    }
  }

  return (
    <>
      <PageHeader
        title={summary}
        subtitle={`验证方式：${basisLabel(basis)}`}
        extra={
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/reports/audits/${taskId}`)}>
              返回任务
            </Button>
            <Button icon={<DownloadOutlined />} onClick={() => exportFile('md')}>
              导出 MD
            </Button>
          </Space>
        }
      />
      <PageContainer>
        {isLoading || !data ? (
          <Card loading />
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card title="漏洞简述" size="small">
              <Paragraph style={{ fontSize: 14, fontWeight: 500 }}>{summary}</Paragraph>
            </Card>
            <Card title="代码与依赖推理链" size="small">
              {reasoning ? <MarkdownBody source={reasoning} /> : <Text type="secondary">暂缺</Text>}
            </Card>
            <Card title="漏洞定位与代码证据" size="small">
              <Descriptions column={2} size="small" bordered>
                <Descriptions.Item label="文件路径" span={2}>
                  <Text code>{String(locus.file_path || '—')}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="函数符号">
                  <Text code>{String(locus.function_symbol || '—')}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="行号区间">
                  <Text code>{String(locus.line_span || locus.line_start || '—')}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="CWE 映射">
                  {locus.cwe ? <Tag color="volcano" className="crucible-cwe-tag">{String(locus.cwe)}</Tag> : '—'}
                </Descriptions.Item>
                <Descriptions.Item label="来源引擎">
                  <Text>{engines}</Text>
                </Descriptions.Item>
              </Descriptions>
            </Card>
            <Card title="终认结论" size="small">
              <Space size="middle">
                {finalVerdict ? (
                  <Tag color={getVerdictMeta(finalVerdict).color} style={{ fontSize: 13, padding: '2px 8px' }}>
                    {getVerdictMeta(finalVerdict).label}
                  </Tag>
                ) : (
                  '—'
                )}
                <Tag color="blue">{basisLabel(basis)}</Tag>
              </Space>
            </Card>
            <Card title="安全修复建议" size="small">
              {remediation ? <MarkdownBody source={remediation} /> : <Text type="secondary">暂无修复建议</Text>}
            </Card>
          </Space>
        )}
      </PageContainer>
    </>
  )
}

