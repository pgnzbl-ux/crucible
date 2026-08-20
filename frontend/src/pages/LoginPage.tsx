import { Button, Card, Form, Input, Typography, Divider, App as AntApp } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, Redirect } from 'wouter'
import { api } from '../shared/lib/api'
import { errorToastText } from '../shared/lib/errorToast'
import { useErrorToast } from '../shared/hooks/useErrorToast'
import { CrucibleLogo } from '../shared/components/CrucibleLogo'

const { Title, Text, Paragraph } = Typography

export function LoginPage() {
  const [location, setLocation] = useLocation()
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()
  const setupQuery = useQuery({
    queryKey: ['auth-setup'],
    queryFn: () => api.authSetup(),
    retry: false,
  })
  const needsSetup = setupQuery.data?.needs_setup === true
  useErrorToast(setupQuery.isError, setupQuery.error, '无法连接认证服务')

  const existingToken = localStorage.getItem('crucible_token')
  if (existingToken && location === '/login') {
    return <Redirect to="/" />
  }

  const persistSession = (token: string, user: unknown, greeting: string) => {
    localStorage.setItem('crucible_token', token)
    localStorage.setItem('crucible_user', JSON.stringify(user))
    message.success(greeting)
    setLocation('/')
  }

  const onLogin = async (values: { email: string; password: string }) => {
    setLoading(true)
    try {
      const res = await api.login(values)
      persistSession(res.access_token, res.user, `欢迎，${res.user.display_name}`)
    } catch (e) {
      message.error(errorToastText(e, '登录失败，请检查邮箱和密码'))
    } finally {
      setLoading(false)
    }
  }

  const onCreateAccount = async (values: {
    email: string
    password: string
    display_name: string
  }) => {
    setLoading(true)
    try {
      await api.register(values)
      const res = await api.login({ email: values.email, password: values.password })
      persistSession(res.access_token, res.user, `欢迎，${res.user.display_name}`)
    } catch (e) {
      message.error(errorToastText(e, '创建账号失败'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="crucible-login">
      <div className="crucible-login-hero">
        <div className="crucible-login-hero-content">
          <CrucibleLogo size={64} className="crucible-login-hero-mark" />
          <div className="crucible-login-hero-title">Crucible</div>
          <Paragraph className="crucible-login-hero-desc">
            坩埚 · AI 驱动的漏洞自动验证平台
            <br />
            在隔离沙箱中自动分析源码、复现漏洞、生成报告
          </Paragraph>
        </div>
      </div>
      <div className="crucible-login-form-panel">
        <Card className="crucible-login-card" variant="borderless">
          <div className="crucible-login-brand">
            <Title level={3} style={{ marginBottom: 4 }}>
              {needsSetup ? '创建账号' : '登录'}
            </Title>
            <Text type="secondary">
              {needsSetup ? '创建后使用该账号登录控制台' : '使用已有账号登录'}
            </Text>
          </div>
          {setupQuery.isLoading ? (
            <Button type="primary" loading block>
              加载中
            </Button>
          ) : needsSetup ? (
            <Form onFinish={onCreateAccount} size="large">
              <Form.Item name="display_name" label="显示名" rules={[{ required: true, message: '请输入显示名' }]}>
                <Input prefix={<UserOutlined />} placeholder="显示名" autoComplete="nickname" />
              </Form.Item>
              <Form.Item name="email" label="邮箱" rules={[{ required: true, type: 'email', message: '请输入邮箱' }]}>
                <Input prefix={<UserOutlined />} placeholder="邮箱" autoComplete="email" />
              </Form.Item>
              <Form.Item
                name="password"
                label="密码"
                rules={[
                  { required: true, message: '请输入密码' },
                  { min: 8, message: '密码至少 8 位' },
                ]}
              >
                <Input.Password
                  prefix={<LockOutlined />}
                  placeholder="密码"
                  autoComplete="new-password"
                />
              </Form.Item>
              <Form.Item>
                <Button type="primary" htmlType="submit" loading={loading} block>
                  创建并登录
                </Button>
              </Form.Item>
            </Form>
          ) : (
            <Form onFinish={onLogin} size="large">
              <Form.Item name="email" label="邮箱" rules={[{ required: true, type: 'email', message: '请输入邮箱' }]}>
                <Input prefix={<UserOutlined />} placeholder="邮箱" autoComplete="email" />
              </Form.Item>
              <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
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
          )}
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
