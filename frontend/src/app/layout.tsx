import type { ReactNode } from 'react'
import { App, Avatar, Dropdown, Layout, Menu, Space, Typography } from 'antd'
import {
  BugOutlined,
  CloudServerOutlined,
  CodeOutlined,
  DashboardOutlined,
  FileProtectOutlined,
  LogoutOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'wouter'

import { BreadcrumbNav } from '../shared/components/BreadcrumbNav'
import { CrucibleLogo } from '../shared/components/CrucibleLogo'
import { SIDER_COLLAPSED_WIDTH, SIDER_WIDTH } from '../styles/theme'

const { Header, Content, Sider } = Layout
const { Text } = Typography

const NAV_ITEMS = [
  {
    key: 'overview',
    label: '总览',
    type: 'group' as const,
    children: [{ key: '/', icon: <DashboardOutlined />, label: '工作台' }],
  },
  {
    key: 'operations',
    label: '业务',
    type: 'group' as const,
    children: [
      { key: '/tasks', icon: <BugOutlined />, label: '任务管理' },
      { key: '/projects', icon: <CodeOutlined />, label: '源码管理' },
      { key: '/labs', icon: <CloudServerOutlined />, label: '靶场管理' },
      { key: '/reports', icon: <FileProtectOutlined />, label: '验证报告' },
    ],
  },
  {
    key: 'system',
    label: '系统',
    type: 'group' as const,
    children: [{ key: '/settings', icon: <SettingOutlined />, label: '设置' }],
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

interface AppLayoutProps {
  children: ReactNode
  /** 页面自行占满可视高度并管理内部滚动（详情页用），外层不再产生页面级滚动条 */
  fill?: boolean
}

export function AppLayout({ children, fill = false }: AppLayoutProps) {
  const [location, navigate] = useLocation()
  const { message } = App.useApp()
  const qc = useQueryClient()
  const user = getCurrentUser()

  const selectedKey = location.startsWith('/tasks')
    ? '/tasks'
    : location.startsWith('/projects')
      ? '/projects'
      : location.startsWith('/labs')
        ? '/labs'
        : location.startsWith('/reports')
          ? '/reports'
          : location === '/'
            ? '/'
            : location.split('?')[0]

  const handleLogout = () => {
    localStorage.removeItem('crucible_token')
    localStorage.removeItem('crucible_user')
    qc.clear()
    message.success('已退出登录')
    navigate('/login')
  }

  const userName = user?.display_name ?? user?.email ?? '未登录'
  const avatarText = userName.slice(0, 1).toUpperCase()

  return (
    <Layout className="crucible-shell" style={{ width: '100%' }}>
      <Sider
        theme="light"
        width={SIDER_WIDTH}
        breakpoint="lg"
        collapsible
        collapsedWidth={SIDER_COLLAPSED_WIDTH}
        className="crucible-sider"
        style={{ height: '100%' }}
      >
        <button type="button" className="crucible-brand" onClick={() => navigate('/')} title="返回工作台">
          <CrucibleLogo size={32} className="crucible-brand-mark" />
          <span className="crucible-brand-text">
            <span className="crucible-brand-name">Crucible</span>
            <span className="crucible-brand-tagline">AI 漏洞验证平台</span>
          </span>
        </button>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={NAV_ITEMS}
          style={{ border: 'none' }}
          onClick={({ key }) => {
            if (key.startsWith('/')) navigate(key)
          }}
        />
      </Sider>
      <Layout style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
        <Header className="crucible-header">
          <div className="crucible-header-left">
            <BreadcrumbNav />
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
        <Content
          className={`crucible-content crucible-page-enter${fill ? ' crucible-content--fill' : ''}`}
          style={{ width: '100%' }}
        >
          <div className="crucible-content-inner">{children}</div>
        </Content>
      </Layout>
    </Layout>
  )
}
