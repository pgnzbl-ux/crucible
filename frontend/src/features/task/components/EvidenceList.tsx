import { App, Button, List, Space, Tag, Typography, Upload } from 'antd'
import type { UploadProps } from 'antd'
import { DownloadOutlined, PaperClipOutlined, UploadOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import type { ReactNode } from 'react'

import { api, type Evidence } from '../../../shared/lib/api'
import { safeHttpUrl } from '../../../shared/lib/safeUrl'
import { useErrorToast } from '../../../shared/hooks/useErrorToast'

const { Text } = Typography

export function EvidenceList({ reportId }: { reportId: string }) {
  const { message } = App.useApp()
  const qc = useQueryClient()

  const { data: evidences, isLoading, isError, error } = useQuery({
    queryKey: ['report-evidences', reportId],
    queryFn: () => api.listEvidences(reportId),
  })
  useErrorToast(isError, error, '证据列表加载失败')

  const uploadProps: UploadProps = {
    multiple: false,
    showUploadList: false,
    customRequest: async (options) => {
      const { file, onSuccess, onError } = options
      try {
        const ev = await api.uploadEvidence(reportId, file as File)
        message.success(`已上传: ${ev.file_name}`)
        qc.invalidateQueries({ queryKey: ['report-evidences', reportId] })
        qc.invalidateQueries({ queryKey: ['task-report'] })
        onSuccess?.(ev)
      } catch (e) {
        message.error((e as Error).message)
        onError?.(e as Error)
      }
    },
  }

  return (
    <div style={{ marginTop: 12 }}>
      <Space style={{ marginBottom: 8, width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary" style={{ fontSize: 13 }}>
          <PaperClipOutlined /> 证据文件（{evidences?.length ?? 0}）
        </Text>
        <Upload {...uploadProps}>
          <Button size="small" icon={<UploadOutlined />}>
            上传证据
          </Button>
        </Upload>
      </Space>
      <List<Evidence>
        size="small"
        loading={isLoading}
        locale={{ emptyText: isError ? '证据列表加载失败' : '暂无证据文件' }}
        dataSource={evidences ?? []}
        renderItem={(ev) => {
          const downloadHref = safeHttpUrl(ev.download_url)
          return (
          <List.Item
            actions={[
              downloadHref ? (
                <Button
                  size="small"
                  type="link"
                  icon={<DownloadOutlined />}
                  href={downloadHref}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  下载
                </Button>
              ) : null,
            ].filter(Boolean) as ReactNode[]}
          >
            <List.Item.Meta
              avatar={<PaperClipOutlined style={{ fontSize: 18, color: 'var(--crucible-text-disabled)' }} />}
              title={
                <Space>
                  <Text style={{ fontSize: 13 }}>{ev.file_name}</Text>
                  <Tag style={{ fontSize: 11 }}>{ev.kind}</Tag>
                </Space>
              }
              description={
                <div>
                  {(ev.kind === 'screenshot' || ev.content_type.startsWith('image/')) && downloadHref ? (
                    <img
                      src={downloadHref}
                      alt={ev.file_name}
                      style={{
                        maxWidth: 280,
                        maxHeight: 160,
                        borderRadius: 6,
                        display: 'block',
                        marginBottom: 8,
                      }}
                    />
                  ) : null}
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {ev.content_type} · {(ev.size_bytes / 1024).toFixed(1)} KB ·{' '}
                    {dayjs(ev.created_at).format('MM-DD HH:mm')}
                  </Text>
                </div>
              }
            />
          </List.Item>
          )
        }}
      />
    </div>
  )
}
