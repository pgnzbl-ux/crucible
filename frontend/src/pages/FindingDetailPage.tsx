import { useState } from 'react'
import { App, Button, Card, Checkbox, Collapse, Descriptions, Input, Modal, Space, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, SendOutlined, UndoOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useParams } from 'wouter'

import { api } from '../shared/lib/api'
import { PageContainer } from '../shared/components/PageContainer'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { formatSourceToSink } from '../shared/lib/findingEvidence'

const { Text, Paragraph } = Typography

export function FindingDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [, navigate] = useLocation()
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [rejectOpen, setRejectOpen] = useState(false)
  const [rejectTags, setRejectTags] = useState<string[]>([])
  const [rejectText, setRejectText] = useState('')
  const [dispatchOpen, setDispatchOpen] = useState(false)
  const [includeEngine, setIncludeEngine] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmText, setConfirmText] = useState('')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['alert-group', id],
    queryFn: () => api.getAlertGroup(id!),
    enabled: !!id,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['alert-group', id] })

  const reviewMutation = useMutation({
    mutationFn: (payload: Parameters<typeof api.reviewAlertGroup>[1]) =>
      api.reviewAlertGroup(id!, payload),
    onSuccess: () => {
      message.success('复核动作已记录')
      setConfirmOpen(false)
      setConfirmText('')
      invalidate()
      qc.invalidateQueries({ queryKey: ['alert-groups'] })
    },
    onError: (e: Error) => message.error(e.message),
    onSettled: () => setRejectOpen(false),
  })

  const reviveMutation = useMutation({
    mutationFn: () => api.reviveAlertGroup(id!),
    onSuccess: () => {
      message.success('已复活到复核队列')
      invalidate()
    },
    onError: (e: Error) => message.error(e.message),
  })

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

  return (
    <PageContainer>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/findings')}>
            返回漏洞线索
          </Button>
          {data.status === 'needs_review' && (
            <>
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={() => setDispatchOpen(true)}
              >
                发起定向验证
              </Button>
              <Button onClick={() => setConfirmOpen(true)}>
                确认漏洞
              </Button>
              <Button danger onClick={() => setRejectOpen(true)}>
                判误报
              </Button>
            </>
          )}
          {data.status === 'resolved' && data.resolution === 'false_positive' && (
            <Button icon={<UndoOutlined />} onClick={() => reviveMutation.mutate()}>
              复活(误杀护栏)
            </Button>
          )}
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
              {data.file_path}
              {data.function_symbol ? ` · ${data.function_symbol}()` : ''}
              {data.line_span ? ` L${data.line_span}` : ''}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag>{data.status}</Tag>
              {data.resolution && <Tag color={data.resolution === 'confirmed' ? 'green' : 'default'}>{data.resolution}</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="AI 判决">
              {data.ai_verdict ? (
                <Space>
                  <Tag color={data.ai_verdict === 'tp' ? 'red' : 'default'}>{data.ai_verdict}</Tag>
                  {data.ai_confidence != null && <Text>{data.ai_confidence.toFixed(2)}</Text>}
                </Space>
              ) : (
                '未审（≠ 误报）'
              )}
            </Descriptions.Item>
            <Descriptions.Item label="引擎">{(data.engine_set ?? []).join(', ')}</Descriptions.Item>
            <Descriptions.Item label="成员数">{data.member_count}</Descriptions.Item>
            {data.verification_task_id && (
              <Descriptions.Item label="定向验证" span={2}>
                <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/tasks/${data.verification_task_id}`)}>
                  {data.verification_task_id.slice(0, 8)}
                </Button>
                {data.verification_verdict && (
                  <Tag style={{ marginLeft: 8 }}>{data.verification_verdict}</Tag>
                )}
              </Descriptions.Item>
            )}
          </Descriptions>
        </Card>

        {rep && (
          <Card title="命中代码(脱敏)" size="small">
            {sourceToSink ? (
              <Paragraph>
                <Text type="secondary">数据流：</Text>
                {sourceToSink}
              </Paragraph>
            ) : null}
            <pre
              style={{
                background: 'var(--crucible-bg)',
                padding: 12,
                borderRadius: 8,
                fontSize: 12,
                overflow: 'auto',
              }}
            >
              {rep.code_snippet ?? rep.message}
            </pre>
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

        {(data.lead_runs ?? []).length > 0 && (
          <Card title="自动终认记录" size="small">
            <Descriptions column={1} size="small">
              {data.lead_runs.map((lead, index) => (
                <Descriptions.Item key={lead.id} label={`线索 ${index + 1}`}>
                  <Space wrap>
                    <Tag>{lead.status}</Tag>
                    {lead.verdict && <Tag color={lead.verdict === 'confirmed' ? 'green' : 'orange'}>{lead.verdict}</Tag>}
                    {lead.gate_verdict && <Text type="secondary">白盒门禁：{lead.gate_verdict}</Text>}
                    {lead.error && <Text type="danger">{lead.error}</Text>}
                  </Space>
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
        )}

        {(data.reviews ?? []).length > 0 && (
          <Card title="复核记录(标注数据)" size="small">
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
          title="确认漏洞"
          open={confirmOpen}
          onOk={() => reviewMutation.mutate({ action: 'confirm', reason_text: confirmText })}
          okButtonProps={{ disabled: !confirmText.trim(), loading: reviewMutation.isPending }}
          onCancel={() => setConfirmOpen(false)}
        >
          <Paragraph type="secondary">请记录确认依据，便于报告审阅和后续规则评估。</Paragraph>
          <Input.TextArea
            rows={3}
            placeholder="例如：用户输入可控，数据流可达危险调用，终认已证明影响"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
          />
        </Modal>

        <Modal
          title="判误报(必须选原因——动作即标注数据)"
          open={rejectOpen}
          onOk={() =>
            reviewMutation.mutate({
              action: 'reject',
              reason_tags: rejectTags,
              reason_text: rejectText || null,
            })
          }
          okButtonProps={{ disabled: rejectTags.length === 0 }}
          onCancel={() => setRejectOpen(false)}
        >
          <Checkbox.Group
            options={[
              '输入不可控',
              '已有净化',
              '路径不可达',
              '测试代码',
              '配置已禁用',
              '其他',
            ]}
            value={rejectTags}
            onChange={(v) => setRejectTags(v as string[])}
          />
          <Input.TextArea
            rows={2}
            style={{ marginTop: 8 }}
            placeholder="补充说明(可选)"
            value={rejectText}
            onChange={(e) => setRejectText(e.target.value)}
          />
        </Modal>

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
            附带引擎原文(默认不含，防止锚定审计结论)
          </Checkbox>
        </Modal>
      </Space>
    </PageContainer>
  )
}
