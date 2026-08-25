import { useState } from 'react'
import { App, Button, Card, Checkbox, Collapse, Descriptions, Modal, Space, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, DeleteOutlined, SendOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useParams } from 'wouter'

import { api } from '../shared/lib/api'
import { downloadAuthenticated } from '../shared/lib/download'
import { PageContainer } from '../shared/components/PageContainer'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { getVerdictMeta, getAiVerdictMeta, formatFindingEngines } from '../shared/lib/meta'
import { displaySourcePath, formatSourceToSink, evidenceMetaFromFinding, findingEvidenceView, ruleClassLabel } from '../shared/lib/findingEvidence'
import { safeHttpUrl } from '../shared/lib/safeUrl'

const { Text, Paragraph } = Typography

export function FindingDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [, navigate] = useLocation()
  const { message, modal } = App.useApp()
  const qc = useQueryClient()
  const [dispatchOpen, setDispatchOpen] = useState(false)
  const [includeEngine, setIncludeEngine] = useState(false)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['alert-group', id],
    queryFn: () => api.getAlertGroup(id!),
    enabled: !!id,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['alert-group', id] })

  useErrorToast(isError, error)

  const dispatchMutation = useMutation({
    mutationFn: () => api.dispatchAlertGroup(id!, includeEngine),
    onSuccess: (r) => {
      message.success('定向验证已创建')
      invalidate()
      navigate(`/tasks/${r.verification_task_id}`)
    },
    onError: (e: Error) => message.error(e.message),
    onSettled: () => setDispatchOpen(false),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteAlertGroup(id!),
    onSuccess: () => {
      message.success('已删除线索')
      qc.invalidateQueries({ queryKey: ['alert-groups'] })
      qc.invalidateQueries({ queryKey: ['finding-stats'] })
      navigate('/findings')
    },
    onError: (e: Error) => message.error(e.message),
  })

  const confirmDelete = () => {
    modal.confirm({
      title: '删除这条漏洞线索？',
      content: '将永久删除线索及其 AI 研判 / 人工复核记录；引擎原始扫描结果会保留。终认进行中时无法删除。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => deleteMutation.mutateAsync(),
    })
  }

  if (isLoading) return <PageContainer><Text>加载中...</Text></PageContainer>
  if (error || !data) {
    return (
      <PageContainer>
        <Text type="danger">漏洞线索不存在或无权访问</Text>
      </PageContainer>
    )
  }

  const rep = data.representative
  const latestAdj = data.adjudications[data.adjudications.length - 1]
  const sourceToSink = formatSourceToSink(rep?.source_to_sink)
  const evidenceMeta = evidenceMetaFromFinding(rep)
  const ruleLabel = ruleClassLabel(evidenceMeta.ruleClass)
  const evidence = rep ? findingEvidenceView(rep) : null

  return (
    <PageContainer>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/findings')}>
            返回漏洞线索
          </Button>
          {data.status !== 'resolved' && (
            <>
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={() => setDispatchOpen(true)}
              >
                发起定向验证
              </Button>
            </>
          )}
          <Button
            danger
            icon={<DeleteOutlined />}
            loading={deleteMutation.isPending}
            onClick={confirmDelete}
          >
            删除
          </Button>
        </Space>

        <Card title="漏洞线索" size="small">
          <Descriptions column={2} size="small">
            <Descriptions.Item label="项目" span={2}>{data.project_address ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="审计运行">
              <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/tasks/${data.task_id}`)}>
                {data.task_id.slice(0, 8)}
              </Button>
            </Descriptions.Item>
            <Descriptions.Item label="源码版本">{data.project_ref ?? '默认版本'}</Descriptions.Item>
            <Descriptions.Item label="CWE">{data.cwe ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="线索等级">{data.clue_grade ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="位置" span={2}>
              {displaySourcePath(data.file_path)}
              {data.function_symbol ? ` · ${data.function_symbol}()` : ''}
              {data.line_span ? ` L${data.line_span}` : ''}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag>{data.status}</Tag>
              {data.resolution && (
                <Tag color={data.resolution === 'confirmed' ? 'red' : 'gold'}>
                  {data.resolution === 'confirmed' ? '已确认' : data.resolution === 'code_reachable' ? '代码可达' : data.resolution}
                </Tag>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="AI 判决">
              {data.ai_verdict ? (
                <Space>
                  <Tag color={getAiVerdictMeta(data.ai_verdict).color}>
                    {getAiVerdictMeta(data.ai_verdict).label}
                  </Tag>
                  {data.ai_confidence != null && <Text>{data.ai_confidence.toFixed(2)}</Text>}
                </Space>
              ) : (
                '尚未研判'
              )}
            </Descriptions.Item>
            <Descriptions.Item label="引擎">{formatFindingEngines(data.engine_set)}</Descriptions.Item>
            <Descriptions.Item label="成员数">{data.member_count}</Descriptions.Item>
            {data.verification_task_id && (
              <Descriptions.Item label="定向验证" span={2}>
                <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/tasks/${data.verification_task_id}`)}>
                  {data.verification_task_id.slice(0, 8)}
                </Button>
                {data.verification_verdict && (
                  <Tag style={{ marginLeft: 8 }}>{getVerdictMeta(data.verification_verdict).label}</Tag>
                )}
              </Descriptions.Item>
            )}
          </Descriptions>
        </Card>

        {rep && evidence && (
          <Card title={evidence.cardTitle} size="small">
            {rep.engine === 'semgrep' ? (
              <Paragraph style={{ marginBottom: 8 }}>
                <Space size={[4, 4]} wrap>
                  <Tag color={evidenceMeta.hasDataflow ? 'blue' : 'default'}>
                    {evidenceMeta.hasDataflow ? '有数据流' : '无数据流'}
                  </Tag>
                  {evidenceMeta.confidence ? (
                    <Tag>规则置信 {evidenceMeta.confidence}</Tag>
                  ) : null}
                  {ruleLabel ? <Tag color={evidenceMeta.ruleClass === 'known' ? 'orange' : 'default'}>{ruleLabel}</Tag> : null}
                </Space>
              </Paragraph>
            ) : null}
            {sourceToSink ? (
              <Paragraph>
                <Text type="secondary">数据流：</Text>
                {sourceToSink}
              </Paragraph>
            ) : null}
            {evidence.fields.length > 0 ? (
              <Descriptions column={1} size="small" style={{ marginBottom: evidence.body ? 8 : 0 }}>
                {evidence.fields.map((field) => (
                  <Descriptions.Item key={field.label} label={field.label}>
                    {field.value}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            ) : null}
            {evidence.redacted ? (
              <Paragraph type="warning">
                当前记录在扫描时被脱敏。请从该任务的敏感信息检测节点重跑，即可查看命中原文。
              </Paragraph>
            ) : null}
            {evidence.body ? (
              <pre
                style={{
                  background: 'var(--crucible-bg)',
                  padding: 12,
                  borderRadius: 8,
                  fontSize: 12,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {evidence.body}
              </pre>
            ) : null}
            {evidence.links.map((link) => {
              const href = safeHttpUrl(link.href)
              if (!href) return null
              return (
                <Paragraph key={href} style={{ marginTop: 8, marginBottom: 0 }}>
                  <a href={href} target="_blank" rel="noreferrer">{link.label}</a>
                </Paragraph>
              )
            })}
          </Card>
        )}

        {latestAdj && (
          <Card title={`AI 研判（第 ${latestAdj.attempt} 轮）`} size="small">
            <Paragraph>
              <Text strong>结论：</Text>
              <Tag color={latestAdj.verdict === 'tp' ? 'red' : latestAdj.verdict === 'fp' ? 'default' : 'orange'}>
                {latestAdj.verdict}
              </Tag>
              {latestAdj.confidence != null && <Text>{latestAdj.confidence.toFixed(2)}</Text>}
            </Paragraph>
            {latestAdj.summary && (
              <Paragraph>
                <Text strong>简述：</Text>
                {latestAdj.summary}
              </Paragraph>
            )}
            {latestAdj.reasoning && (
              <Paragraph>
                <Text strong>推理：</Text>
                <span style={{ whiteSpace: 'pre-wrap' }}>{latestAdj.reasoning}</span>
              </Paragraph>
            )}
            <Paragraph>
              <Text strong>理由：</Text>
              <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                {(latestAdj.why ?? []).map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </Paragraph>
            {(latestAdj.evidence ?? []).length > 0 && (
              <Paragraph>
                <Text strong>证据：</Text>
                {latestAdj.evidence.map((e, i) => (
                  <Tag key={i}>
                    {e.file}
                    {e.lines ? ` L${e.lines}` : ''}
                  </Tag>
                ))}
              </Paragraph>
            )}
            {(latestAdj.need ?? []).length > 0 && (
              <Paragraph type="secondary">缺失上下文：{latestAdj.need.join('；')}</Paragraph>
            )}
            <Collapse
              size="small"
              items={[{
                key: 'replay',
                label: '查看模型输入与原始响应',
                children: (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Text strong>Prompt</Text>
                    <pre style={{ whiteSpace: 'pre-wrap' }}>{latestAdj.prompt_text}</pre>
                    <Text strong>Response</Text>
                    <pre style={{ whiteSpace: 'pre-wrap' }}>{latestAdj.response_text}</pre>
                  </Space>
                ),
              }]}
            />
          </Card>
        )}

        {data.vuln_report && (
          <Card
            title="独立漏洞报告"
            size="small"
            extra={
              <Space>
                {data.verification_basis && (
                  <Tag>{data.verification_basis === 'lab' ? '靶场验证' : '代码闭环'}</Tag>
                )}
                <Button
                  size="small"
                  onClick={async () => {
                    try {
                      await downloadAuthenticated(
                        api.exportFindingVulnUrl(data.id, 'md'),
                        `vuln-${data.id.slice(0, 8)}.md`,
                      )
                    } catch (e) {
                      message.error(e instanceof Error ? e.message : '导出失败')
                    }
                  }}
                >
                  导出
                </Button>
              </Space>
            }
          >
            <Paragraph>
              <Text strong>简述：</Text>
              {String(data.vuln_report.summary || '—')}
            </Paragraph>
            <Paragraph>
              <Text strong>终认：</Text>
              {String(data.vuln_report.final_verdict || data.resolution || '—')}
            </Paragraph>
            <Button
              type="link"
              style={{ padding: 0 }}
              onClick={() => navigate(`/reports/audits/${data.task_id}/vulns/${data.id}`)}
            >
              在漏洞报告中打开
            </Button>
          </Card>
        )}

        {(data.lead_runs ?? []).length > 0 && (
          <Card title="自动终认记录" size="small">
            <Descriptions column={1} size="small">
              {data.lead_runs.map((lead, index) => (
                <Descriptions.Item key={lead.id} label={`线索 ${index + 1}`}>
                  <Space wrap>
                    <Tag>{lead.status}</Tag>
                    {lead.verdict && (
                      <Tag color={lead.verdict === 'confirmed' ? 'red' : 'gold'}>
                        {getVerdictMeta(lead.verdict).label}
                      </Tag>
                    )}
                    {lead.gate_verdict && <Text type="secondary">白盒结论：{lead.gate_verdict}</Text>}
                    {lead.error && <Text type="danger">{lead.error}</Text>}
                  </Space>
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
        )}

        {(data.reviews ?? []).length > 0 && (
          <Card title="复核记录" size="small">
            {data.reviews.map((r) => (
              <Paragraph key={r.id}>
                <Tag>{r.action}</Tag>
                {r.reason_tags?.map((t) => (
                  <Tag key={t} color="blue">
                    {t}
                  </Tag>
                ))}
                {r.reason_text}
              </Paragraph>
            ))}
          </Card>
        )}

        <Modal
          title="发起定向验证"
          open={dispatchOpen}
          onOk={() => dispatchMutation.mutate()}
          okButtonProps={{ loading: dispatchMutation.isPending }}
          onCancel={() => setDispatchOpen(false)}
        >
          <Checkbox
            checked={includeEngine}
            onChange={(e) => setIncludeEngine(e.target.checked)}
          >
            附带引擎原始告警（默认关闭，避免影响独立研判）
          </Checkbox>
        </Modal>
      </Space>
    </PageContainer>
  )
}
