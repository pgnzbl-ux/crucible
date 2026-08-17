import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { Button, Result } from 'antd'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
}

/**
 * 全局异常兜底：渲染友好错误页 + 重新加载按钮
 * 挂在 main.tsx 的根节点外层
 */
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[AppErrorBoundary]', error, info)
  }

  handleReset = () => {
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      return (
        <Result
          status="500"
          title="页面出错了"
          subTitle="发生未预期的异常，请尝试重新加载。"
          extra={
            <Button type="primary" onClick={this.handleReset}>
              重新加载
            </Button>
          }
        />
      )
    }
    return this.props.children
  }
}
