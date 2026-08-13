import { useMemo } from 'react'
import { Column } from '@ant-design/charts'
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

  const config = {
    data,
    xField: 'date',
    yField: 'count',
    color: '#1677ff',
    columnStyle: { radius: [4, 4, 0, 0] },
    label: {
      position: 'top' as const,
      style: { fill: 'rgba(0,0,0,0.45)', fontSize: 12 },
    },
    xAxis: { label: { style: { fill: 'rgba(0,0,0,0.45)' } } },
    yAxis: {
      minInterval: 1,
      label: { style: { fill: 'rgba(0,0,0,0.45)' } },
    },
    meta: {
      count: { alias: '新建任务数' },
      date: { alias: '日期' },
    },
    height: 260,
  }

  return <Column {...config} />
}
