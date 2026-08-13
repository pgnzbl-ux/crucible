import { Button, Card, Form, Input, Typography, Divider, App as AntApp } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useState } from 'react'
import { useLocation, Redirect } from 'wouter'
import { SafetyCertificateOutlined } from '@ant-design/icons'
import { api } from '../shared/lib/api'

const { Title, Text, Paragraph } = Typography

export function LoginPage() {
  const [location, setLocation] = useLocation()
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()

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
      setTimeout(() => window.location.reload(), 50)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="crucible-login">
      <div className="crucible-login-hero">
        <div className="crucible-login-hero-content">
          <span className="crucible-login-hero-mark">
            <SafetyCertificateOutlined />
          </span>
          <div className="crucible-login-hero-title">Crucible</div>
          <Paragraph className="crucible-login-hero-desc">
            坩埚 · AI 驱动的漏洞自动验证平台
            <br />
            在隔离沙箱中自动分析源码、复现漏洞、生成报告
          </Paragraph>
        </div>
      </div>
      <div className="crucible-login-form-panel">
        <Card className="crucible-login-card" bordered={false}>
          <div className="crucible-login-brand">
            <Title level={3} style={{ marginBottom: 4 }}>
              登录
            </Title>
            <Text type="secondary">使用平台账号登录</Text>
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
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="密码"
                autoComplete="current-password"
              />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={loading} block>
                登录
              </Button>
            </Form.Item>
          </Form>
          <Divider plain style={{ margin: '8px 0 0' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              安全登录 · 凭证仅用于平台鉴权
            </Text>
          </Divider>
        </Card>
      </div>
    </div>
  )
}
