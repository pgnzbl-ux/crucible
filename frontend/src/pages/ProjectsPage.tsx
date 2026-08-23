import { useState } from 'react'
import { App, Button, Dropdown, Empty, Space, Table, Tooltip, Typography } from 'antd'
import {
  BranchesOutlined,
  BugOutlined,
  CheckCircleFilled,
  CloudUploadOutlined,
  DeleteOutlined,
  FileZipOutlined,
  GithubOutlined,
  MoreOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useLocation } from 'wouter'

import { api, type Project } from '../shared/lib/api'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { PageHeader } from '../shared/components/PageHeader'
import { PageContainer } from '../shared/components/PageContainer'
import { tableRowNavigateProps } from '../shared/lib/tableRowNavigate'
import { RegisterSourceDrawer } from '../features/project/RegisterSourceDrawer'

const { Text } = Typography

const refTypeLabels: Record<string, string> = {
  branch: '分支',
  tag: '标签',
  commit: '提交',
  upload: '上传版本',
}

function cleanRepositoryAddress(address: string) {
  return address.replace(/^https?:\/\//, '').replace(/\.git$/, '').replace(/\/$/, '')
}

export function ProjectsPage() {
  const { message, modal } = App.useApp()
  const qc = useQueryClient()
  const [, navigate] = useLocation()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [registerMode, setRegisterMode] = useState<'git' | 'upload' | null>(null)

  const { data, error, isError, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['projects', { page, pageSize }],
    queryFn: () =>
      api.listProjects({ limit: String(pageSize), offset: String((page - 1) * pageSize) }),
    placeholderData: keepPreviousData,
  })
  useErrorToast(isError, error, '项目列表加载失败')

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteProject(id),
    onSuccess: () => {
      message.success('项目已删除')
      void qc.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const columns: ColumnsType<Project> = [
    {
      title: '项目',
      dataIndex: 'name',
      width: '34%',
      render: (name: string, row) => (
        <div className="crucible-project-asset__identity">
          <span
            className={`crucible-project-asset__source-icon${row.source_type === 'local_upload' ? ' is-upload' : ''}`}
            aria-hidden="true"
          >
            {row.source_type === 'local_upload' ? <FileZipOutlined /> : <GithubOutlined />}
          </span>
          <div className="crucible-project-asset__identity-body">
            <Text strong className="crucible-project-asset__name">
              {name}
            </Text>
            <div className="crucible-project-asset__meta">
              <span>{row.source_type === 'local_upload' ? '本地源码包' : 'Git 仓库'}</span>
              <span className="crucible-project-asset__meta-separator" aria-hidden="true" />
              {row.source_type === 'local_upload' ? (
                <Text type="secondary" ellipsis className="crucible-project-asset__address">
                  {row.description?.trim() || '由上传归档创建'}
                </Text>
              ) : (
                <Tooltip title={row.git_url} placement="topLeft">
                  <Text type="secondary" ellipsis className="crucible-project-asset__address">
                    {cleanRepositoryAddress(row.git_url)}
                  </Text>
                </Tooltip>
              )}
            </div>
          </div>
        </div>
      ),
    },
    {
      title: '技术栈',
      width: '27%',
      render: (_, row) => {
        const hasProfile = row.is_web != null || row.detected_language || row.detected_framework
        return (
          <div className="crucible-project-asset__stack">
            {hasProfile ? (
              <>
                <span className={`crucible-project-asset__pill${row.is_web === true ? ' is-web' : ''}`}>
                  {row.is_web == null ? '待画像' : row.is_web ? 'Web' : '非 Web'}
                </span>
                {row.detected_language ? (
                  <span className="crucible-project-asset__pill is-language">{row.detected_language}</span>
                ) : null}
                {row.detected_framework ? (
                  <Tooltip title={row.detected_framework} placement="topLeft">
                    <Text type="secondary" ellipsis className="crucible-project-asset__framework">
                      {row.detected_framework}
                    </Text>
                  </Tooltip>
                ) : null}
              </>
            ) : (
              <Text type="secondary" className="crucible-project-asset__unprofiled">
                首次审计后自动画像
              </Text>
            )}
          </div>
        )
      },
    },
    {
      title: '审计版本',
      dataIndex: 'default_ref',
      width: '19%',
      render: (v: string | null, row) => (
        <div className="crucible-project-asset__version">
          <BranchesOutlined className="crucible-project-asset__version-icon" />
          <div className="crucible-project-asset__version-body">
            <Text type="secondary" className="crucible-project-asset__eyebrow">
              {v ? refTypeLabels[row.default_ref_type ?? ''] ?? '版本' : '默认版本'}
            </Text>
            <Tooltip title={v || 'HEAD'} placement="topLeft">
              <Text ellipsis className="crucible-project-asset__ref">
                {v || 'HEAD'}
              </Text>
            </Tooltip>
          </div>
        </div>
      ),
    },
    {
      title: '源码状态',
      dataIndex: 'last_cloned_at',
      width: 164,
      render: (v: string | null) => (
        <div className="crucible-project-asset__state">
          <span className={`crucible-project-asset__state-label${v ? ' is-ready' : ''}`}>
            {v ? <CheckCircleFilled /> : <span className="crucible-project-asset__state-dot" aria-hidden="true" />}
            {v ? '已获取源码' : '等待获取'}
          </span>
          <Text type="secondary" className="crucible-project-asset__state-time">
            {v ? `更新于 ${dayjs(v).format('MM-DD HH:mm')}` : '审计时自动获取'}
          </Text>
        </div>
      ),
    },
    {
      title: '操作',
      width: 150,
      fixed: 'right',
      render: (_, row) => (
        <Space size={2} className="crucible-project-asset__actions" onClick={(e) => e.stopPropagation()}>
          <Button size="small" type="link" icon={<BugOutlined />} onClick={() => navigate(`/projects/${row.id}`)}>
            查看并审计
          </Button>
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                {
                  key: 'delete',
                  icon: <DeleteOutlined />,
                  label: '删除项目',
                  danger: true,
                  onClick: () =>
                    modal.confirm({
                      title: '删除该项目？',
                      content: '将删除登记信息与本仓库缓存包。进行中的任务工作目录不受影响。',
                      okText: '删除',
                      okType: 'danger',
                      cancelText: '返回',
                      onOk: () => deleteMutation.mutateAsync(row.id),
                    }),
                },
              ],
            }}
          >
            <Button
              size="small"
              type="text"
              icon={<MoreOutlined />}
              aria-label={`更多项目操作：${row.name}`}
              loading={deleteMutation.isPending && deleteMutation.variables === row.id}
            />
          </Dropdown>
        </Space>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="项目资产"
        subtitle="登记待审计的 Git 仓库或源码包，并管理可重复审计的版本快照"
        extra={
          <Space>
            <Button icon={<GithubOutlined />} onClick={() => setRegisterMode('git')}>
              登记 Git
            </Button>
            <Button icon={<CloudUploadOutlined />} onClick={() => setRegisterMode('upload')}>
              上传源码包
            </Button>
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
          </Space>
        }
      />
      <PageContainer className="crucible-project-assets">
        <div className="crucible-project-assets__bar">
          <div className="crucible-project-assets__bar-title">
            <span>资产清单</span>
            <span className="crucible-project-assets__count">{data?.total ?? 0}</span>
          </div>
          <Text type="secondary">点击项目行可查看版本快照与审计入口</Text>
        </div>
        <Table
          className="crucible-project-assets__table"
          rowKey="id"
          loading={isLoading || isFetching}
          dataSource={data?.items ?? []}
          columns={columns}
          scroll={{ x: 1080 }}
          pagination={{
            current: page,
            pageSize,
            total: data?.total ?? 0,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50],
            showTotal: (total) => `共 ${total} 条`,
            onChange: (nextPage, nextPageSize) => {
              setPage(nextPageSize === pageSize ? nextPage : 1)
              setPageSize(nextPageSize)
            },
          }}
          onRow={(row) => tableRowNavigateProps(() => navigate(`/projects/${row.id}`))}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="还没有项目资产。先登记 Git 仓库或上传源码包，再发起代码审计。"
              >
                <Space>
                  <Button icon={<GithubOutlined />} onClick={() => setRegisterMode('git')}>
                    登记 Git
                  </Button>
                  <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setRegisterMode('upload')}>
                    上传源码包
                  </Button>
                </Space>
              </Empty>
            ),
          }}
        />
      </PageContainer>
      <RegisterSourceDrawer
        open={registerMode !== null}
        mode={registerMode ?? 'git'}
        onClose={() => setRegisterMode(null)}
      />
    </>
  )
}
