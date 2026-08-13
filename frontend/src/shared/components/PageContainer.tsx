import type { ReactNode } from 'react'

interface PageContainerProps {
  children: ReactNode
  className?: string
}

/** 统一页面内容容器：白底卡片 + 圆角 + 阴影 */
export function PageContainer({ children, className }: PageContainerProps) {
  return <div className={`crucible-page-container${className ? ` ${className}` : ''}`}>{children}</div>
}
