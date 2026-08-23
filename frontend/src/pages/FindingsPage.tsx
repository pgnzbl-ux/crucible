import { useMemo, useState } from 'react'
import { Button, Input, Segmented, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useLocation, useSearch } from 'wouter'
import dayjs from 'dayjs'

import { api, type AlertGroupSummary } from '../shared/lib/api'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { getPriorityMeta } from '../shared/lib/meta'
import { findingStatusLabel, projectLabel, screeningStatusMeta, sourceVersionLabel } from '../shared/lib/tablePresentation'

const { Text } = Typography

const STATUS_META: Record<string, { label: string; color: string }> = {
  new: { label: '新建', color: 'default' },
  clustered: { label: '已聚类', color: 'default' },
  adjudicated: { label: '已裁决', color: 'processing' },
  needs_review: { label: '待复核', color: 'warning' },
  dispatched: { label: '终认中', color: 'processing' },
  resolved: { label: '已处置', color: 'success' },
}

const VERDICT_META: Record<string, { label: string; color: string }> = {
  tp: { label: '疑似真实', color: 'red' },
  fp: { label: 'AI 判误报', color: 'default' },
  need_more_context: { label: '上下文不足', color: 'orange' },
  bypass: { label: '依赖情报', color: 'blue' },
}

const GRADE_META: Record<string, { label: string; color: string }> = {
  A: { label: 'A · 证据充分', color: 'red' },
  B: { label: 'B · 建议复核', color: 'orange' },
  F: { label: 'F · 疑似误报', color: 'default' },
}

function severityMeta(severity: string | null) {
  const normalized = (severity ?? '').toLowerCase()
  if (normalized === 'critical') return { label: '严重', color: 'red' }
  if (normalized === 'high' || normalized === 'error') return { label: '高风险', color: 'volcano' }
  if (normalized === 'medium' || normalized === 'warning') return { label: '中风险', color: 'orange' }
  if (normalized === 'low' || normalized === 'note') return { label: '低风险', color: 'blue' }
  return null
}

const PAGE_SIZE = 20
type FindingScope = 'focus' | 'review' | 'processing' | 'noise' | 'all'

export function FindingsPage() {
  const [, navigate] = useLocation()
  const search = useSearch()
  const [status, setStatus] = useState<string | undefined>(
    () => new URLSearchParams(search).get('status') ?? undefined,
  )
  const [scope, setScope] = useState<FindingScope>(() => {
    const query = new URLSearchParams(search)
    const requested = query.get('scope')
    if (requested && ['focus', 'review', 'processing', 'noise', 'all'].includes(requested)) {
      return requested as FindingScope
    }
    return query.get('status') ? 'all' : 'focus'
  })
  const [aiVerdict, setAiVerdict] = useState<string | undefined>(undefined)
  const [clueGrade, setClueGrade] = useState<string | undefined>(undefined)
  const [engine, setEngine] = useState<string | undefined>(undefined)
  const [cwe, setCwe] = useState('')
  const [page, setPage] = useState(1)

  const params = useMemo(
    () => ({
      status,
      scope,
      ai_verdict: aiVerdict,
      clue_grade: clueGrade,
      engine,
      cwe: cwe.trim() || undefined,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }),
    [status, scope, aiVerdict, clueGrade, engine, cwe, page],
  )

  const { data, isError, error, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['alert-groups', params],
    queryFn: () => api.listAlertGroups(params as Record<string, string | number>),
    placeholderData: keepPreviousData,
  })
  const {
    data: stats,
    isError: isStatsError,
    error: statsError,
    isFetching: isStatsFetching,
    refetch: refetchStats,
  } = useQuery({
    queryKey: ['finding-stats'],
    queryFn: () => api.getFindingStats(),
  })

  useErrorToast(isError, error)
  useErrorToast(isStatsError, statsError, '漏洞线索统计加载失败')

  const scopeOptions = [
    { value: 'focus', label: `重点线索 ${stats?.by_queue.focus ?? 0}` },
    { value: 'review', label: `需人工复核 ${stats?.by_queue.review ?? 0}` },
    { value: 'processing', label: `初筛中 ${stats?.by_queue.processing ?? 0}` },
    { value: 'noise', label: `已降噪 ${stats?.by_queue.noise ?? 0}` },
    { value: 'all', label: `全部 ${stats?.total ?? 0}` },
  ]

  const columns = [
    {
      title: '漏洞类型 / 风险',
      dataIndex: 'cwe',
      width: 180,
      ellipsis: true,
      render: (v: string | null, row: AlertGroupSummary) => (
        <div style={{ minWidth: 0 }}>
          <Tooltip
            placement="topLeft"
            title={(
              <div>
                <div>{row.vulnerability_title}</div>
                {row.representative_rule_id ? <div>规则：{row.representative_rule_id}</div> : null}
                {row.representative_message ? <div>{row.representative_message}</div> : null}
              </div>
            )}
          >
            <Text strong ellipsis style={{ display: 'block', width: '100%' }}>
              {row.vulnerability_title}
            </Text>
          </Tooltip>
          <div>
            <Tag color={v ? (row.cwe_source === 'inferred' ? 'gold' : 'volcano') : 'default'} bordered={false}>
              {v ? `${row.cwe_source === 'inferred' ? '推断 ' : ''}${v}` : '未映射 CWE'}
            </Tag>
            {severityMeta(row.severity) ? <Tag color={severityMeta(row.severity)!.color} bordered={false}>{severityMeta(row.severity)!.label}</Tag> : null}
            {!row.severity && row.priority ? <Tag color={getPriorityMeta(row.priority).color} bordered={false}>{getPriorityMeta(row.priority).label}优先级</Tag> : null}
          </div>
        </div>
      ),
    },
    {
      title: '项目 / 版本',
      dataIndex: 'project_address',
      width: 210,
      ellipsis: true,
      render: (v: string | null, row: AlertGroupSummary) => (
        <div>
          <Text>{projectLabel(v)}</Text>
          <div><Text type="secondary" style={{ fontSize: 12 }}>{sourceVersionLabel(row.project_ref, null)} · 审计 {row.task_id.slice(0, 8)}</Text></div>
        </div>
      ),
    },
    {
      title: '位置',
      dataIndex: 'file_path',
      ellipsis: true,
      render: (v: string, row: AlertGroupSummary) => (
        <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => navigate(`/findings/${row.id}`)}>
          {v}
          {row.function_symbol ? ` · ${row.function_symbol}()` : ''}
          {row.line_span ? ` L${row.line_span}` : ''}
        </Button>
      ),
    },
    {
      title: '证据概况',
      dataIndex: 'clue_grade',
      width: 170,
      render: (v: string | null, row: AlertGroupSummary) => {
        const meta = v ? GRADE_META[v] : undefined
        return (
          <div>
            <Tag color={meta?.color ?? 'default'}>{meta?.label ?? '证据待评估'}</Tag>
            <div><Text type="secondary" style={{ fontSize: 12 }}>{row.member_count > 1 ? `已合并 ${row.member_count} 个规则命中` : '单个规则命中'} · {(row.engine_set ?? []).join(' + ') || '未知引擎'}</Text></div>
          </div>
        )
      },
    },
    {
      title: '初筛处理',
      dataIndex: 'screening_summary',
      width: 220,
      render: (_: string, row: AlertGroupSummary) => {
        const meta = screeningStatusMeta(row.screening_status)
        const reasons = row.screening_reasons ?? []
        return (
          <Tooltip
            placement="topLeft"
            title={(
              <div>
                <div>{row.screening_summary}</div>
                {reasons.map((reason, index) => <div key={`${reason}-${index}`}>· {reason}</div>)}
              </div>
            )}
          >
            <div style={{ minWidth: 0 }}>
              <Space size={4}>
                <Tag color={meta.color}>{meta.label}</Tag>
                <Text strong>{row.screening_summary}</Text>
                {row.ai_confidence != null ? <Text type="secondary">{Math.round(row.ai_confidence * 100)}%</Text> : null}
              </Space>
              <Text type="secondary" ellipsis style={{ display: 'block', width: '100%', fontSize: 12 }}>
                {reasons[0] ?? '暂无初筛说明'}
              </Text>
            </div>
          </Tooltip>
        )
      },
    },
    {
      title: '处理状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string, row: AlertGroupSummary) => {
        const meta = STATUS_META[v] ?? { label: v, color: 'default' }
        return (
          <Tag color={row.resolution === 'confirmed' ? 'red' : row.resolution === 'false_positive' ? 'green' : meta.color}>
            {findingStatusLabel(v, row.resolution)}
          </Tag>
        )
      },
    },
    {
      title: '最近更新',
      dataIndex: 'updated_at',
      width: 130,
      render: (v: string | null) => v ? dayjs(v).format('MM-DD HH:mm') : '—',
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_: unknown, row: AlertGroupSummary) => (
        <Button size="small" icon={<EyeOutlined />} onClick={() => navigate(`/findings/${row.id}`)}>
          {row.status === 'needs_review' ? '开始复核' : row.status === 'dispatched' ? '查看终认' : '查看详情'}
        </Button>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="漏洞线索"
        subtitle="默认只呈现初筛保留的重点线索；原始规则命中会先聚类、研判和降噪"
        extra={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => void Promise.all([refetch(), refetchStats()])}
            loading={isFetching || isStatsFetching}
          >
            刷新
          </Button>
        }
      />
      <div className="crucible-filter-bar">
        <Space wrap>
          <Segmented
            value={scope}
            options={scopeOptions}
            onChange={(value) => { setScope(value as FindingScope); setPage(1) }}
          />
          <Select
            allowClear
            placeholder="处理状态"
            style={{ width: 150 }}
            value={status}
            onChange={(v) => {
              setStatus(v)
              setPage(1)
            }}
            options={Object.entries(STATUS_META).map(([value, m]) => ({ value, label: m.label }))}
          />
          <Select
            allowClear
            placeholder="证据强度"
            style={{ width: 140 }}
            value={clueGrade}
            onChange={(v) => { setClueGrade(v); setPage(1) }}
            options={['A', 'B', 'F'].map((value) => ({ value, label: GRADE_META[value]?.label ?? `${value} 级` }))}
          />
          <Select
            allowClear
            placeholder="发现引擎"
            style={{ width: 140 }}
            value={engine}
            onChange={(v) => { setEngine(v); setPage(1) }}
            options={['semgrep', 'gitleaks', 'osv'].map((value) => ({ value, label: value }))}
          />
          <Input
            allowClear
            placeholder="CWE，如 CWE-89"
            style={{ width: 170 }}
            value={cwe}
            onChange={(e) => { setCwe(e.target.value); setPage(1) }}
          />
          <Select
            allowClear
            placeholder="AI 研判"
            style={{ width: 140 }}
            value={aiVerdict}
            onChange={(v) => {
              setAiVerdict(v)
              setPage(1)
            }}
            options={Object.entries(VERDICT_META).map(([value, m]) => ({ value, label: m.label }))}
          />
        </Space>
      </div>
      <PageContainer>
        <Table
          rowKey="id"
          size="middle"
          loading={isLoading || isFetching}
          columns={columns}
          dataSource={data?.items ?? []}
          scroll={{ x: 1320 }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: data?.total ?? 0,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条`,
            onChange: setPage,
          }}
        />
      </PageContainer>
    </>
  )
}
