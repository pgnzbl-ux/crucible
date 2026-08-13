import { Button, DatePicker, Input, Select, Space } from 'antd'
import { SearchOutlined, ClearOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

import type { TaskListParams } from '../hooks/useTaskListParams'

const { RangePicker } = DatePicker

interface TaskFilterBarProps {
  params: TaskListParams
  onChange: (next: Partial<TaskListParams>) => void
  onClear: () => void
}

const STATUS_OPTIONS = [
  { value: 'queued', label: '排队中' },
  { value: 'pending', label: '待处理' },
  { value: 'running', label: '分析中' },
  { value: 'needs_review', label: '待复核' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

const PRIORITY_OPTIONS = [
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
  { value: 'critical', label: '严重' },
]

export function TaskFilterBar({ params, onChange, onClear }: TaskFilterBarProps) {
  const dateRange =
    params.dateFrom || params.dateTo
      ? [params.dateFrom ? dayjs(params.dateFrom) : null, params.dateTo ? dayjs(params.dateTo) : null]
      : null

  return (
    <div className="crucible-filter-bar">
      <Space wrap size="middle">
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 140 }}
          value={params.status}
          onChange={(v) => onChange({ status: v })}
          options={STATUS_OPTIONS}
        />
        <Select
          allowClear
          placeholder="优先级"
          style={{ width: 120 }}
          value={params.priority}
          onChange={(v) => onChange({ priority: v })}
          options={PRIORITY_OPTIONS}
        />
        <RangePicker
          placeholder={['开始日期', '结束日期']}
          value={dateRange as [dayjs.Dayjs | null, dayjs.Dayjs | null] | null}
          onChange={(dates) => {
            onChange({
              dateFrom: dates?.[0]?.format('YYYY-MM-DD'),
              dateTo: dates?.[1]?.format('YYYY-MM-DD'),
            })
          }}
        />
        <Input
          allowClear
          placeholder="搜索项目地址"
          prefix={<SearchOutlined />}
          style={{ width: 220 }}
          value={params.q ?? ''}
          onChange={(e) => onChange({ q: e.target.value || undefined })}
          onPressEnter={(e) => onChange({ q: (e.target as HTMLInputElement).value || undefined })}
        />
        <Button icon={<ClearOutlined />} onClick={onClear}>
          重置
        </Button>
      </Space>
    </div>
  )
}
