import { useMemo } from 'react'
import dayjs from 'dayjs'

import type { TaskSummary } from '../../../shared/lib/api'

interface TaskTrendChartProps {
  tasks: TaskSummary[]
}

export function TaskTrendChart({ tasks }: TaskTrendChartProps) {
  const data = useMemo(() => {
    const days: Record<string, { date: string; count: number }> = {}
    for (let i = 6; i >= 0; i--) {
      const d = dayjs().subtract(i, 'day').format('MM-DD')
      days[d] = { date: d, count: 0 }
    }
    for (const t of tasks) {
      const d = dayjs(t.created_at).format('MM-DD')
      if (days[d]) {
        days[d].count += 1
      }
    }
    return Object.values(days)
  }, [tasks])

  const maxCount = Math.max(1, ...data.map((item) => item.count))
  const accessibleSummary = data.map((item) => `${item.date} ${item.count} 个`).join('，')

  return (
    <div
      role="img"
      aria-label={`近 7 日任务趋势：${accessibleSummary}`}
      style={{
        display: 'flex',
        alignItems: 'flex-end',
        gap: 12,
        height: 260,
        padding: '16px 8px 0',
        borderBottom: '1px solid var(--crucible-border)',
      }}
    >
      {data.map((item) => {
        const barHeight = item.count === 0 ? 2 : Math.max(8, (item.count / maxCount) * 170)
        return (
          <div
            key={item.date}
            style={{ display: 'flex', flex: 1, minWidth: 0, flexDirection: 'column', alignItems: 'center' }}
          >
            <span style={{ color: 'var(--crucible-text-secondary)', fontSize: 12 }}>{item.count}</span>
            <div style={{ height: 180, display: 'flex', alignItems: 'flex-end', justifyContent: 'center', width: '100%' }}>
              <div
                style={{
                  width: '70%',
                  height: barHeight,
                  minHeight: 2,
                  background: item.count ? 'var(--crucible-primary)' : 'var(--crucible-border)',
                  borderRadius: '4px 4px 0 0',
                  transition: 'height 240ms ease-out',
                }}
              />
            </div>
            <span style={{ color: 'var(--crucible-text-secondary)', fontSize: 12 }}>{item.date}</span>
          </div>
        )
      })}
    </div>
  )
}
