import { App, Button, List, Space, Tag, Typography, Upload } from 'antd'
import type { UploadProps } from 'antd'
import { DownloadOutlined, PaperClipOutlined, UploadOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { api, type Evidence } from '../../../shared/lib/api'

const { Text } = Typography

export function EvidenceList({ reportId }: { reportId: string }) {
  const { message } = App.useApp()
  const qc = useQueryClient()

  const { data: evidences, isLoading } = useQuery({
    queryKey: ['report-evidences', reportId],
    queryFn: () => api.listEvidences(reportId),
  })

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
        locale={{ emptyText: '暂无证据文件' }}
        dataSource={evidences ?? []}
        renderItem={(ev) => (
          <List.Item
            actions={[
              ev.download_url ? (
                <Button
                  size="small"
                  type="link"
                  icon={<DownloadOutlined />}
                  href={ev.download_url}
                  target="_blank"
                >
                  下载
                </Button>
              ) : null,
            ].filter(Boolean) as React.ReactNode[]}
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
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {ev.content_type} · {(ev.size_bytes / 1024).toFixed(1)} KB ·{' '}
                  {dayjs(ev.created_at).format('MM-DD HH:mm')}
                </Text>
              }
            />
          </List.Item>
        )}
      />
    </div>
  )
}
