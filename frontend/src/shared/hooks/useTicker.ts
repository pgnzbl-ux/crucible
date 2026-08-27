import { useEffect, useState } from 'react'

/**
 * 秒级心跳时钟：仅在 enabled 时启动 interval，返回当前时刻（epoch 毫秒）。
 *
 * 用途：运行中节点的"已进行"耗时不能依赖数据刷新驱动——SSE 连通期间轮询
 * 是关闭的，没有这个钟，计时会冻结在最近一次事件到达的时刻。
 * 关闭时不挂定时器，终态列表零开销。
 */
export function useTicker(enabled: boolean, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!enabled) return
    // 立即校准一次，避免启动瞬间用旧值渲染出 +1s 偏差
    setNow(Date.now())
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [enabled, intervalMs])

  return now
}
