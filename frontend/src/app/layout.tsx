import type { ReactNode } from 'react'
import { App, Avatar, Dropdown, Layout, Menu, Space, Typography } from 'antd'
import {
  BugOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  FileProtectOutlined,
  LogoutOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { Link, useLocation } from 'wouter'

import { BreadcrumbNav } from '../shared/components/BreadcrumbNav'
import { getRouteMeta } from '../shared/lib/routes'

const { Header, Content, Sider } = Layout
const { Text } = Typography

const NAV_GROUPS = [
  {
    key: 'overview',
    label: '总览',
    type: 'group' as const,
    children: [{ key: '/', icon: <DashboardOutlined />, label: <Link to="/">工作台</Link> }],
  },
  {
    key: 'operations',
    label: '业务',
    type: 'group' as const,
    children: [
      { key: '/tasks', icon: <BugOutlined />, label: <Link to="/tasks">任务管理</Link> },
      { key: '/labs', icon: <CloudServerOutlined />, label: <Link to="/labs">靶场管理</Link> },
      { key: '/reports', icon: <FileProtectOutlined />, label: <Link to="/reports">验证报告</Link> },
    ],
  },
  {
    key: 'system',
    label: '系统',
    type: 'group' as const,
    children: [{ key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">设置</Link> }],
  },
]

interface CurrentUser {
  display_name?: string
  email?: string
  role?: string
}

function getCurrentUser(): CurrentUser | null {
  try {
    const raw = localStorage.getItem('crucible_user')
    return raw ? (JSON.parse(raw) as CurrentUser) : null
  } catch {
    return null
  }
}

export function AppLayout({ children }: { children: ReactNode }) {
  const [location] = useLocation()
  const { message } = App.useApp()
  const user = getCurrentUser()

  const selectedKey = location.startsWith('/tasks/')
    ? '/tasks'
    : location.startsWith('/labs')
      ? '/labs'
      : location === '/'
        ? '/'
        : location.split('?')[0]
  const pageMeta = getRouteMeta(location)

  const handleLogout = () => {
    localStorage.removeItem('crucible_token')
    localStorage.removeItem('crucible_user')
    message.success('已退出登录')
    window.location.href = '/login'
  }

  const userName = user?.display_name ?? user?.email ?? '未登录'
  const avatarText = userName.slice(0, 1).toUpperCase()

  return (
    <Layout style={{ minHeight: '100vh', width: '100%' }}>
      <Sider
        theme="light"
        width={224}
        breakpoint="lg"
        collapsible
        collapsedWidth={64}
        className="crucible-sider"
        style={{ position: 'sticky', top: 0, height: '100vh' }}
      >
        <div className="crucible-brand">
          <span className="crucible-brand-mark">
            <SafetyCertificateOutlined />
          </span>
          <span className="crucible-brand-text">
            <span className="crucible-brand-name">Crucible</span>
            <span className="crucible-brand-tagline">AI 漏洞验证平台</span>
          </span>
        </div>
        <Menu mode="inline" selectedKeys={[selectedKey]} items={NAV_GROUPS} style={{ border: 'none' }} />
      </Sider>
      <Layout style={{ flex: 1, minWidth: 0 }}>
        <Header className="crucible-header">
          <div className="crucible-header-left">
            <BreadcrumbNav />
            <Text className="crucible-header-title">/ {pageMeta.title}</Text>
          </div>
          <Dropdown
            menu={{
              items: [
                {
                  key: 'user-info',
                  label: (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {user?.email ?? ''}
                    </Text>
                  ),
                  disabled: true,
                },
                { type: 'divider' },
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  onClick: handleLogout,
                },
              ],
            }}
            placement="bottomRight"
          >
            <Space className="crucible-header-user">
              <Avatar size={28} style={{ background: 'var(--crucible-primary)', fontSize: 13 }}>
                {avatarText}
              </Avatar>
              <span className="crucible-header-username">{userName}</span>
            </Space>
          </Dropdown>
        </Header>
        <Content className="crucible-content crucible-page-enter" style={{ width: '100%' }}>
          <div className="crucible-content-inner">{children}</div>
        </Content>
      </Layout>
    </Layout>
  )
}
