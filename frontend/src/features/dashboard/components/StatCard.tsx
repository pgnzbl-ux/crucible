import type { ReactNode } from 'react'
import { Card } from 'antd'

interface StatCardProps {
  title: string
  value: number | string
  icon: ReactNode
  tone?: 'primary' | 'success' | 'warning' | 'error' | 'default'
  trend?: string
  onClick?: () => void
}

export function StatCard({ title, value, icon, tone = 'default', trend, onClick }: StatCardProps) {
  return (
    <Card
      className="stat-card"
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                onClick()
              }
            }
          : undefined
      }
    >
      <div className={`stat-card-icon stat-card-icon-${tone}`}>{icon}</div>
      <div className="stat-card-title">{title}</div>
      <div className="stat-card-value">{value}</div>
      {trend && <div className="stat-card-trend">{trend}</div>}
    </Card>
  )
}
