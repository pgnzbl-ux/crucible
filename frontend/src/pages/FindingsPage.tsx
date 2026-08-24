import { useEffect, useMemo, useState } from 'react'
import { App, Button, Empty, Input, Popconfirm, Segmented, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { ClearOutlined, DeleteOutlined, EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useSearch } from 'wouter'
import dayjs from 'dayjs'

import { api, type AlertGroupSummary } from '../shared/lib/api'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { getPriorityMeta } from '../shared/lib/meta'
import { findingStatusLabel, projectLabel, screeningStatusMeta, sourceVersionLabel } from '../shared/lib/tablePresentation'
import {
  buildFindingsSearch,
  FINDING_PROGRESS_OPTIONS,
  parseFindingProgress,
  parseFindingScope,
  progressToParams,
  type FindingProgressValue,
  type FindingScope,
} from '../shared/lib/findingsListQuery'
import { tableRowNavigateProps } from '../shared/lib/tableRowNavigate'

const { Text } = Typography

const STATUS_TAG_COLOR: Record<string, string> = {
  new: 'default',
  clustered: 'default',
  adjudicated: 'processing',
  needs_review: 'warning',
  dispatched: 'processing',
  resolved: 'success',
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

const ENGINE_LABELS: Record<string, string> = {
  semgrep: 'Semgrep 静态',
  gitleaks: 'Gitleaks 密钥',
  osv: 'OSV 依赖',
}

const PAGE_SIZE = 20
const BATCH_DELETE_CHUNK = 100

async function batchDeleteChunked(ids: string[]) {
  const deleted: string[] = []
  const skipped: { id: string; reason: string }[] = []
  for (let i = 0; i < ids.length; i += BATCH_DELETE_CHUNK) {
    const chunk = ids.slice(i, i + BATCH_DELETE_CHUNK)
    const result = await api.batchDeleteAlertGroups(chunk)
    deleted.push(...result.deleted)
    skipped.push(...result.skipped)
  }
  return { deleted, skipped }
}

function severityMeta(severity: string | null) {
  const normalized = (severity ?? '').toLowerCase()
  if (normalized === 'critical') return { label: '严重', color: 'red' }
  if (normalized === 'high' || normalized === 'error') return { label: '高风险', color: 'volcano' }
  if (normalized === 'medium' || normalized === 'warning') return { label: '中风险', color: 'orange' }
  if (normalized === 'low' || normalized === 'note') return { label: '低风险', color: 'blue' }
  return null
}

export function FindingsPage() {
  const { message, modal } = App.useApp()
  const qc = useQueryClient()
  const [, navigate] = useLocation()
  const search = useSearch()
  const query = useMemo(() => new URLSearchParams(search), [search])

  const [scope, setScope] = useState<FindingScope>(() => parseFindingScope(query))
  const [progress, setProgress] = useState<FindingProgressValue | undefined>(() => parseFindingProgress(query))
  const [keyword, setKeyword] = useState(() => query.get('q') ?? '')
  const [debouncedQ, setDebouncedQ] = useState(() => (query.get('q') ?? '').trim())
  const [aiVerdict, setAiVerdict] = useState<string | undefined>(() => query.get('verdict') ?? undefined)
  const [clueGrade, setClueGrade] = useState<string | undefined>(() => query.get('grade') ?? undefined)
  const [engine, setEngine] = useState<string | undefined>(() => query.get('engine') ?? undefined)
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [page, setPage] = useState(() => {
    const raw = Number(query.get('page') || '1')
    return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1
  })

  const progressParams = useMemo(() => progressToParams(progress), [progress])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const next = keyword.trim()
      setDebouncedQ((prev) => {
        if (prev === next) return prev
        setPage(1)
        return next
      })
    }, 300)
    return () => window.clearTimeout(timer)
  }, [keyword])

  useEffect(() => {
    const href = buildFindingsSearch({
      scope,
      status: progressParams.status,
      resolution: progressParams.resolution,
      q: debouncedQ || undefined,
      engine,
      clueGrade,
      aiVerdict,
      page,
    })
    const current = search ? `/findings?${search}` : '/findings'
    if (href !== current) navigate(href, { replace: true })
  }, [scope, progressParams, debouncedQ, engine, clueGrade, aiVerdict, page, navigate, search])

  const params = useMemo(
    () => ({
      status: progressParams.status,
      resolution: progressParams.resolution,
      scope,
      ai_verdict: aiVerdict,
      clue_grade: clueGrade,
      engine,
      q: debouncedQ || undefined,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }),
    [progressParams, scope, aiVerdict, clueGrade, engine, debouncedQ, page],
  )

  const filterParams = useMemo(
    () => ({
      status: progressParams.status,
      resolution: progressParams.resolution,
      scope,
      ai_verdict: aiVerdict,
      clue_grade: clueGrade,
      engine,
      q: debouncedQ || undefined,
    }),
    [progressParams, scope, aiVerdict, clueGrade, engine, debouncedQ],
  )

  const filterKey = useMemo(() => JSON.stringify(filterParams), [filterParams])

  useEffect(() => {
    setSelectedRowKeys([])
  }, [filterKey])

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

  const refreshLists = () => {
    void refetch()
    void refetchStats()
    void qc.invalidateQueries({ queryKey: ['alert-groups'] })
    void qc.invalidateQueries({ queryKey: ['finding-stats'] })
  }

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteAlertGroup(id),
    onSuccess: (_data, id) => {
      message.success('已删除线索')
      setSelectedRowKeys((keys) => keys.filter((k) => k !== id))
      refreshLists()
    },
    onError: (e: Error) => message.error(e.message),
  })

  const batchDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => batchDeleteChunked(ids),
    onSuccess: (result) => {
      const blocked = result.skipped.filter((s) => s.reason === 'in_progress').length
      if (result.deleted.length && blocked) {
        message.warning(`已删除 ${result.deleted.length} 条；${blocked} 条终认中未删`)
      } else if (result.deleted.length) {
        message.success(`已删除 ${result.deleted.length} 条线索`)
      } else if (blocked) {
        message.warning('选中线索均在终认中，无法删除')
      } else {
        message.info('没有可删除的线索')
      }
      setSelectedRowKeys([])
      refreshLists()
    },
    onError: (e: Error) => message.error(e.message),
  })

  const selectAllFilteredMutation = useMutation({
    mutationFn: () => api.listAlertGroupIds(filterParams as Record<string, string | number>),
    onSuccess: (result) => {
      setSelectedRowKeys(result.ids)
      message.success(`已跨页选中全部 ${result.ids.length} 条`)
    },
    onError: (e: Error) => message.error(e.message),
  })

  const confirmBatchDelete = () => {
    const ids = selectedRowKeys.map(String)
    if (!ids.length) return
    modal.confirm({
      title: `删除选中的 ${ids.length} 条线索？`,
      content: '将永久删除线索及其 AI 研判 / 人工复核记录；引擎原始扫描结果会保留。终认进行中的条目会自动跳过。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => batchDeleteMutation.mutateAsync(ids),
    })
  }

  const pageIds = useMemo(() => (data?.items ?? []).map((item) => item.id), [data?.items])
  const totalFiltered = data?.total ?? 0
  const selectedAllFiltered = totalFiltered > 0 && selectedRowKeys.length === totalFiltered
  const pageFullySelected = pageIds.length > 0 && pageIds.every((id) => selectedRowKeys.includes(id))
  const showCrossPageHint = totalFiltered > pageIds.length

  const hasExtraFilters = Boolean(progress || debouncedQ || aiVerdict || clueGrade || engine)
  const clearFilters = () => {
    setScope('focus')
    setProgress(undefined)
    setKeyword('')
    setDebouncedQ('')
    setAiVerdict(undefined)
    setClueGrade(undefined)
    setEngine(undefined)
    setSelectedRowKeys([])
    setPage(1)
  }

  const scopeOptions = [
    { value: 'focus', label: `重点 ${stats?.by_queue.focus ?? 0}` },
    { value: 'review', label: `待复核 ${stats?.by_queue.review ?? 0}` },
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
        <Text>
          {v}
          {row.function_symbol ? ` · ${row.function_symbol}()` : ''}
          {row.line_span ? ` L${row.line_span}` : ''}
        </Text>
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
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {row.member_count > 1 ? `已合并 ${row.member_count} 个规则命中` : '单个规则命中'}
                {' · '}
                {(row.engine_set ?? []).map((e) => ENGINE_LABELS[e] ?? e).join(' + ') || '未知引擎'}
              </Text>
            </div>
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
      width: 120,
      render: (v: string, row: AlertGroupSummary) => {
        const color = row.resolution === 'confirmed'
          ? 'red'
          : row.resolution === 'false_positive'
            ? 'green'
            : row.resolution === 'ignored'
              ? 'default'
              : (STATUS_TAG_COLOR[v] ?? 'default')
        return <Tag color={color}>{findingStatusLabel(v, row.resolution)}</Tag>
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
      width: 168,
      render: (_: unknown, row: AlertGroupSummary) => (
        <Space size={4} onClick={(event) => event.stopPropagation()}>
          <Button
            size="small"
            type={row.status === 'needs_review' ? 'primary' : 'default'}
            icon={<EyeOutlined />}
            onClick={() => navigate(`/findings/${row.id}`)}
          >
            {row.status === 'needs_review' ? '复核' : row.status === 'dispatched' ? '终认' : '详情'}
          </Button>
          <Popconfirm
            title="删除这条线索？"
            description="永久删除；终认进行中时会失败。"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => deleteMutation.mutateAsync(row.id)}
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={deleteMutation.isPending && deleteMutation.variables === row.id}
            />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const emptyDescription = hasExtraFilters
    ? '当前筛选下没有线索，试试清空条件或换个工作队列'
    : scope === 'focus'
      ? '暂无重点线索。新扫描完成后会出现在这里；也可查看「待复核」或「全部」'
      : '这个队列暂时是空的'

  return (
    <>
      <PageHeader
        title="漏洞线索"
        subtitle="先选工作队列处理待办，再用搜索定位具体文件 / CWE / 项目"
        extra={
          <Space>
            {selectedRowKeys.length > 0 ? (
              <Button
                danger
                icon={<DeleteOutlined />}
                loading={batchDeleteMutation.isPending}
                onClick={confirmBatchDelete}
              >
                删除选中 ({selectedRowKeys.length})
              </Button>
            ) : null}
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void Promise.all([refetch(), refetchStats()])}
              loading={isFetching || isStatsFetching}
            >
              刷新
            </Button>
          </Space>
        }
      />
      <div className="crucible-filter-bar" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
          <Segmented
            value={scope}
            options={scopeOptions}
            onChange={(value) => { setScope(value as FindingScope); setPage(1) }}
          />
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索路径 / CWE / 项目 / 函数 / 审计 ID"
            style={{ width: 320, maxWidth: '100%' }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
          {hasExtraFilters || scope !== 'focus' ? (
            <Button icon={<ClearOutlined />} onClick={clearFilters}>
              清空条件
            </Button>
          ) : null}
        </div>
        <Space wrap size={[8, 8]}>
          <Select
            allowClear
            placeholder="发现引擎"
            style={{ width: 150 }}
            value={engine}
            onChange={(v) => { setEngine(v); setPage(1) }}
            options={Object.entries(ENGINE_LABELS).map(([value, label]) => ({ value, label }))}
          />
          <Select
            allowClear
            placeholder="证据强度"
            style={{ width: 150 }}
            value={clueGrade}
            onChange={(v) => { setClueGrade(v); setPage(1) }}
            options={['A', 'B', 'F'].map((value) => ({ value, label: GRADE_META[value]?.label ?? `${value} 级` }))}
          />
          <Select
            allowClear
            placeholder="AI 研判"
            style={{ width: 140 }}
            value={aiVerdict}
            onChange={(v) => { setAiVerdict(v); setPage(1) }}
            options={Object.entries(VERDICT_META).map(([value, m]) => ({ value, label: m.label }))}
          />
          <Select
            allowClear
            placeholder="处理状态"
            style={{ width: 150 }}
            value={progress}
            onChange={(v) => { setProgress(v as FindingProgressValue | undefined); setPage(1) }}
            options={FINDING_PROGRESS_OPTIONS}
          />
        </Space>
      </div>
      {selectedRowKeys.length > 0 || showCrossPageHint ? (
        <div
          className="crucible-filter-bar"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 8,
            alignItems: 'center',
            marginTop: -4,
            marginBottom: 8,
          }}
        >
          {selectedAllFiltered ? (
            <Text>
              已跨页选中当前筛选全部 <Text strong>{selectedRowKeys.length}</Text> 条
            </Text>
          ) : pageFullySelected && showCrossPageHint ? (
            <>
              <Text>已选本页 {pageIds.length} 条。</Text>
              <Button
                type="link"
                style={{ padding: 0, height: 'auto' }}
                loading={selectAllFilteredMutation.isPending}
                onClick={() => selectAllFilteredMutation.mutate()}
              >
                选择全部 {totalFiltered} 条
              </Button>
            </>
          ) : selectedRowKeys.length > 0 ? (
            <Text>
              已选 <Text strong>{selectedRowKeys.length}</Text> 条
              {showCrossPageHint ? '（可跨页勾选）' : ''}
            </Text>
          ) : showCrossPageHint ? (
            <Button
              type="link"
              style={{ padding: 0, height: 'auto' }}
              loading={selectAllFilteredMutation.isPending}
              onClick={() => selectAllFilteredMutation.mutate()}
            >
              跨页全选当前筛选（{totalFiltered} 条）
            </Button>
          ) : null}
          {selectedRowKeys.length > 0 ? (
            <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => setSelectedRowKeys([])}>
              清空选择
            </Button>
          ) : null}
        </div>
      ) : null}
      <PageContainer>
        <Table
          rowKey="id"
          size="middle"
          loading={isLoading || isFetching || selectAllFilteredMutation.isPending}
          columns={columns}
          dataSource={data?.items ?? []}
          scroll={{ x: 1400 }}
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys,
            preserveSelectedRowKeys: true,
            selections: [
              {
                key: 'all-filtered',
                text: totalFiltered > 0 ? `跨页全选（${totalFiltered}）` : '跨页全选',
                onSelect: () => { selectAllFilteredMutation.mutate() },
              },
              {
                key: 'clear',
                text: '清空选择',
                onSelect: () => { setSelectedRowKeys([]) },
              },
            ],
          }}
          locale={{
            emptyText: (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyDescription}>
                {hasExtraFilters || scope !== 'focus' ? (
                  <Button type="link" onClick={clearFilters}>清空条件并回到重点队列</Button>
                ) : null}
              </Empty>
            ),
          }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: data?.total ?? 0,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条`,
            onChange: setPage,
          }}
          onRow={(row) => tableRowNavigateProps(() => navigate(`/findings/${row.id}`))}
        />
      </PageContainer>
    </>
  )
}
