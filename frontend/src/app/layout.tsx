import type { ReactNode } from 'react'
import { Layout, Menu, Typography } from 'antd'
import {
  DashboardOutlined,
  BugOutlined,
  FileProtectOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { Link, useLocation } from 'wouter'

const { Header, Content, Sider } = Layout

const NAV_ITEMS = [
  { key: '/', icon: <DashboardOutlined />, label: <Link to="/">工作台</Link> },
  { key: '/tasks', icon: <BugOutlined />, label: <Link to="/tasks">任务管理</Link> },
  { key: '/reports', icon: <FileProtectOutlined />, label: <Link to="/reports">验证报告</Link> },
  { key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">设置</Link> },
]

export function AppLayout({ children }: { children: ReactNode }) {
  const [location] = useLocation()

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="dark" width={220} style={{ position: 'sticky', top: 0, height: '100vh' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '16px 20px',
            color: '#fff',
            fontSize: 16,
            fontWeight: 700,
          }}
        >
          <SafetyCertificateOutlined style={{ color: '#1976d2', fontSize: 22 }} />
          Crucible
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location === '/' ? '/' : location.split('?')[0]]}
          items={NAV_ITEMS}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#1f1f1f',
            borderBottom: '1px solid #303030',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 24px',
          }}
        >
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            AI 漏洞自动验证平台
          </Typography.Text>
          <Typography.Text style={{ fontSize: 13 }}>Mock 模式 · 开发环境</Typography.Text>
        </Header>
        <Content style={{ padding: 24 }}>{children}</Content>
      </Layout>
    </Layout>
  )
}
