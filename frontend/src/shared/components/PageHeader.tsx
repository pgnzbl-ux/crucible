import type { ReactNode } from 'react'
import { Space, Typography } from 'antd'

const { Title, Text } = Typography

interface PageHeaderProps {
  title: ReactNode
  subtitle?: ReactNode
  extra?: ReactNode
}

/**
 * 统一页头：标题 + 副标题 + 右侧操作区
 * 4 个业务页面复用，保证页首层级一致
 */
export function PageHeader({ title, subtitle, extra }: PageHeaderProps) {
  return (
    <div className="crucible-page-header">
      <div className="crucible-page-header-main">
        <Title level={3} style={{ marginBottom: subtitle ? 4 : 0 }}>
          {title}
        </Title>
        {subtitle && <Text type="secondary">{subtitle}</Text>}
      </div>
      {extra && <Space className="crucible-page-header-extra">{extra}</Space>}
    </div>
  )
}
