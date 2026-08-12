import { Button, Result } from 'antd'
import { useLocation } from 'wouter'

export function NotFoundPage() {
  const [, navigate] = useLocation()
  return (
    <Result
      status="404"
      title="404"
      subTitle="抱歉，你访问的页面不存在。"
      extra={
        <Button type="primary" onClick={() => navigate('/')}>
          返回工作台
        </Button>
      }
    />
  )
}
