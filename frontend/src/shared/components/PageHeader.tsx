import type { ReactNode } from 'react'
import { Space, Tabs, Typography } from 'antd'
import type { TabsProps } from 'antd'

const { Title, Text } = Typography

interface PageHeaderProps {
  title: ReactNode
  subtitle?: ReactNode
  extra?: ReactNode
  tabList?: TabsProps['items']
  activeTabKey?: string
  onTabChange?: (key: string) => void
}

/**
 * 统一页头：标题 + 副标题 + 右侧操作区 + 可选 Tab
 */
export function PageHeader({ title, subtitle, extra, tabList, activeTabKey, onTabChange }: PageHeaderProps) {
  return (
    <div className="crucible-page-header">
      <div className="crucible-page-header-main">
        <Title level={3} style={{ marginBottom: subtitle ? 4 : 0 }}>
          {title}
        </Title>
        {subtitle && <Text type="secondary">{subtitle}</Text>}
        {tabList && tabList.length > 0 && (
          <Tabs
            activeKey={activeTabKey}
            onChange={onTabChange}
            items={tabList}
            style={{ marginTop: 16 }}
          />
        )}
      </div>
      {extra && <Space className="crucible-page-header-extra">{extra}</Space>}
    </div>
  )
}
