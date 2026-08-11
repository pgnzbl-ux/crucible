import { Button, Card, Form, Input, Typography, App as AntApp } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useState } from 'react'
import { useLocation, Redirect } from 'wouter'
import { api } from '../shared/lib/api'

const { Title } = Typography

export function LoginPage() {
  const [location, setLocation] = useLocation()
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()

  // 已登录直接进首页
  const existingToken = localStorage.getItem('crucible_token')
  if (existingToken && location === '/login') {
    return <Redirect to="/" />
  }

  const onFinish = async (values: { email: string; password: string }) => {
    setLoading(true)
    try {
      const res = await api.login(values)
      localStorage.setItem('crucible_token', res.access_token)
      localStorage.setItem('crucible_user', JSON.stringify(res.user))
      message.success(`欢迎，${res.user.display_name}`)
      setLocation('/')
      // 强制刷新以触发 RequireAuth 重新判定 + 各页面重新拉数据
      setTimeout(() => window.location.reload(), 50)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', background: '#141414' }}>
      <Card style={{ width: 400 }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Title level={2} style={{ margin: 0 }}>Crucible</Title>
          <Typography.Text type="secondary">坩埚 — AI 漏洞验证平台</Typography.Text>
        </div>
        <Form
          onFinish={onFinish}
          size="large"
          initialValues={{ email: 'admin@crucible.local', password: 'crucible123' }}
        >
          <Form.Item name="email" rules={[{ required: true, message: '请输入邮箱' }]}>
            <Input prefix={<UserOutlined />} placeholder="邮箱" autoComplete="email" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}
