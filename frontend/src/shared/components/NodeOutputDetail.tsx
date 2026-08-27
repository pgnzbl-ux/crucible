import { nodeMetrics, type NodeStatusOutputLike } from '../lib/nodeOutput'

interface NodeOutputDetailProps {
  node: NodeStatusOutputLike
}

/** 已完成节点的关键指标小卡片；无指标（含专用详情面板节点）返回 null。 */
export function NodeOutputDetail({ node }: NodeOutputDetailProps) {
  const metrics = nodeMetrics(node)
  if (metrics.length === 0) return null
  return (
    <div className="crucible-node-metrics" aria-label="节点产出指标">
      {metrics.map((metric) => (
        <span className="crucible-node-metrics__item" key={metric.label}>
          <small>{metric.label}</small>
          <em>{metric.value}</em>
        </span>
      ))}
    </div>
  )
}
