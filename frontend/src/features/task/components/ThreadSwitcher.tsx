import { useState } from 'react'
import { Badge, Button, Popover, Typography } from 'antd'
import { LoadingOutlined, MessageOutlined, TeamOutlined } from '@ant-design/icons'

import { MAIN_THREAD, type ThreadInfo } from '../../../shared/lib/streamRows'

const { Text } = Typography

export interface ThreadLike {
  id: string
  label: string
  status: string
  count: number
}

/** 徽标状态判定（纯函数，便于测试）：失败/错误→红；已完成→绿；运行中无终态→蓝脉冲；其余→灰。 */
export function threadBadgeStatus(
  th: ThreadLike & { isError?: boolean },
  running: boolean,
): 'processing' | 'success' | 'error' | 'default' {
  if (th.status === 'failed' || th.isError === true) return 'error'
  if (th.status === 'completed') return 'success'
  if (running && (!th.status || th.status === 'running' || th.status === 'in_progress')) {
    return 'processing'
  }
  return 'default'
}

function StatusDot({ th, running }: { th: ThreadLike & { isError?: boolean }; running: boolean }) {
  const status = threadBadgeStatus(th, running)
  return (
    <Badge
      status={status}
      style={{ marginLeft: 'auto', flex: '0 0 auto' }}
      title={
        status === 'processing'
          ? '运行中'
          : status === 'success'
            ? '已完成'
            : status === 'error'
              ? '执行失败'
              : ''
      }
    />
  )
}

/** 子代理清单（Popover 内容；导出以便静态标记测试）。 */
export function ThreadMenu({
  threads,
  running,
  value,
  onSelect,
}: {
  threads: ThreadInfo[]
  running: boolean
  value: string
  onSelect: (thread: string) => void
}) {
  return (
    <div className="crucible-thread-menu" role="listbox" aria-label="子代理线程列表">
      <div
        role="option"
        aria-selected={value === MAIN_THREAD}
        className={`crucible-thread-menu__item${value === MAIN_THREAD ? ' is-active' : ''}`}
        data-thread="main"
        onClick={() => onSelect(MAIN_THREAD)}
      >
        <MessageOutlined className="crucible-thread-menu__icon" />
        <span className="crucible-thread-menu__label">主 Agent</span>
      </div>
      {threads.map((th) => (
        <div
          key={th.id}
          role="option"
          aria-selected={value === th.id}
          className={`crucible-thread-menu__item${value === th.id ? ' is-active' : ''}`}
          data-thread={th.id}
          onClick={() => onSelect(th.id)}
        >
          <TeamOutlined className="crucible-thread-menu__icon" />
          <span className="crucible-thread-menu__label" title={th.label || th.id}>
            {th.label || th.id}
          </span>
          {th.count > 0 && (
            <Text type="secondary" className="crucible-thread-menu__count">
              {th.count}
            </Text>
          )}
          <StatusDot th={th} running={running} />
        </div>
      ))}
    </div>
  )
}

/**
 * 子代理运行入口：工具栏上的单点标记——有运行中的子代理时图标转圈，
 * 点击弹出全部子代理清单（含状态点），选择即切换该线程的事件流。
 * 取代原先会无限堆叠的芯片行。
 */
export function ThreadSwitcher({
  threads,
  running,
  value,
  onChange,
}: {
  threads: ThreadInfo[]
  running: boolean
  value: string
  onChange: (thread: string) => void
}) {
  const [open, setOpen] = useState(false)
  const runningCount = threads.filter((th) => threadBadgeStatus(th, running) === 'processing').length

  return (
    <Popover
      placement="bottomRight"
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      title="子代理线程"
      content={
        <ThreadMenu
          threads={threads}
          running={running}
          value={value}
          onSelect={(id) => {
            onChange(id)
            setOpen(false)
          }}
        />
      }
    >
      <Button
        size="small"
        data-testid="thread-switcher"
        icon={
          runningCount > 0 ? (
            <LoadingOutlined spin style={{ color: 'var(--crucible-primary)' }} />
          ) : (
            <TeamOutlined />
          )
        }
      >
        子代理 {threads.length}
        {runningCount > 0 ? ` · 运行 ${runningCount}` : ''}
      </Button>
    </Popover>
  )
}
