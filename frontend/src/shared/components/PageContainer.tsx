import type { ReactNode } from 'react'

interface PageContainerProps {
  children: ReactNode
  className?: string
  /** 撑满父级剩余高度，内部自行滚动 */
  fill?: boolean
}

/** 统一页面内容容器：白底卡片 + 圆角 + 阴影 */
export function PageContainer({ children, className, fill = false }: PageContainerProps) {
  const classes = [
    'crucible-page-container',
    fill ? 'crucible-page-container--fill' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')
  return <div className={classes}>{children}</div>
}
