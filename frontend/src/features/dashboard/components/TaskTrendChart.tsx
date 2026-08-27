import { useMemo } from 'react'
import { Tooltip } from 'antd'
import dayjs from 'dayjs'

import type { TaskSummary } from '../../../shared/lib/api'

export const TREND_SAMPLE_LIMIT = 200
export const TREND_SAMPLE_NOTE = `基于最近 ${TREND_SAMPLE_LIMIT} 条`

export function trendChartAriaLabel(summary: string): string {
  return `近 7 日任务趋势（${TREND_SAMPLE_NOTE}）：${summary}`
}

interface TaskTrendChartProps {
  tasks: TaskSummary[]
}

export function TaskTrendChart({ tasks }: TaskTrendChartProps) {
  const data = useMemo(() => {
    const days: Record<string, { date: string; fullDate: string; count: number; completed: number; failed: number }> = {}
    for (let i = 6; i >= 0; i--) {
      const dObj = dayjs().subtract(i, 'day')
      const d = dObj.format('MM-DD')
      days[d] = { date: d, fullDate: dObj.format('YYYY-MM-DD'), count: 0, completed: 0, failed: 0 }
    }
    for (const t of tasks) {
      const d = dayjs(t.created_at).format('MM-DD')
      if (days[d]) {
        days[d].count += 1
        if (t.status === 'completed') days[d].completed += 1
        if (t.status === 'failed') days[d].failed += 1
      }
    }
    return Object.values(days)
  }, [tasks])

  const maxCount = Math.max(1, ...data.map((item) => item.count))
  const accessibleSummary = data.map((item) => `${item.date} ${item.count} 个`).join('，')

  return (
    <div
      role="img"
      aria-label={trendChartAriaLabel(accessibleSummary)}
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
        const isPeak = item.count === maxCount && item.count > 0
        const barHeight = item.count === 0 ? 3 : Math.max(12, (item.count / maxCount) * 170)
        return (
          <div
            key={item.date}
            style={{ display: 'flex', flex: 1, minWidth: 0, flexDirection: 'column', alignItems: 'center' }}
          >
            <span
              style={{
                color: isPeak ? 'var(--crucible-primary)' : 'var(--crucible-text-secondary)',
                fontSize: 12,
                fontWeight: isPeak ? 700 : 500,
                marginBottom: 2,
              }}
            >
              {item.count}
            </span>
            <div style={{ height: 180, display: 'flex', alignItems: 'flex-end', justifyContent: 'center', width: '100%' }}>
              <Tooltip
                title={
                  <div>
                    <div><strong>{item.fullDate}</strong></div>
                    <div>发起审计：{item.count} 次</div>
                    {item.completed > 0 && <div style={{ color: '#52c41a' }}>已完成：{item.completed}</div>}
                    {item.failed > 0 && <div style={{ color: '#ff4d4f' }}>失败：{item.failed}</div>}
                  </div>
                }
              >
                <div
                  style={{
                    width: '68%',
                    maxWidth: 42,
                    height: barHeight,
                    minHeight: 3,
                    background: item.count
                      ? isPeak
                        ? 'linear-gradient(180deg, #1677ff 0%, #36cfc9 100%)'
                        : 'linear-gradient(180deg, #4096ff 0%, #1677ff 100%)'
                      : 'var(--crucible-border)',
                    borderRadius: '6px 6px 0 0',
                    cursor: 'pointer',
                    boxShadow: item.count ? '0 2px 6px rgba(22, 119, 255, 0.2)' : 'none',
                    transition: 'all 240ms ease-out',
                  }}
                />
              </Tooltip>
            </div>
            <span style={{ color: 'var(--crucible-text-secondary)', fontSize: 12, marginTop: 4 }}>{item.date}</span>
          </div>
        )
      })}
    </div>
  )
}

