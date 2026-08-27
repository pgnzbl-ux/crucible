import { Tag, Typography } from 'antd'

const { Text } = Typography

interface LeadPhase {
  node_key?: string
  status?: string
  attempt?: number
  error?: string | null
}

interface LeadSummary {
  id?: string
  status?: string
  verdict?: string | null
  gate_verdict?: string | null
  verification_basis?: string | null
  queue_position?: number
  phases?: LeadPhase[]
}

interface LeadVerifyDetailProps {
  output?: Record<string, unknown> | null
}

const PHASE_LABEL: Record<string, string> = {
  audit: '白盒',
  reproduce: '复现',
}

export function LeadVerifyDetail({ output }: LeadVerifyDetailProps) {
  if (!output) return null
  const leads = Array.isArray(output.leads) ? (output.leads as LeadSummary[]) : []
  const completed = typeof output.completed_count === 'number' ? output.completed_count : null
  const failed = typeof output.failed_count === 'number' ? output.failed_count : null
  const skipped = typeof output.skipped_count === 'number' ? output.skipped_count : null
  const total = typeof output.lead_count === 'number' ? output.lead_count : leads.length

  return (
    <div className="crucible-lead-verify-detail">
      <p className="crucible-node-list__summary">
        线索 {total}
        {completed != null ? ` · 完成 ${completed}` : ''}
        {failed != null && failed > 0 ? ` · 失败 ${failed}` : ''}
        {skipped != null && skipped > 0 ? ` · 跳过 ${skipped}` : ''}
      </p>
      {leads.length > 0 && (
        <ul className="crucible-lead-verify-detail__list">
          {leads.map((lead, index) => (
            <li key={lead.id ?? String(index)} className="crucible-lead-verify-detail__item">
              <div className="crucible-lead-verify-detail__head">
                <Text strong>#{(lead.queue_position ?? index) + 1}</Text>
                <Tag>{lead.status || 'unknown'}</Tag>
                {lead.verdict ? <Tag color="blue">{lead.verdict}</Tag> : null}
                {lead.gate_verdict ? <Tag>gate:{lead.gate_verdict}</Tag> : null}
                {lead.verification_basis ? <Tag>{lead.verification_basis}</Tag> : null}
              </div>
              {(lead.phases ?? []).length > 0 && (
                <div className="crucible-lead-verify-detail__phases">
                  {(lead.phases ?? []).map((phase) => (
                    <span key={`${lead.id}-${phase.node_key}-${phase.attempt}`}>
                      {PHASE_LABEL[phase.node_key || ''] || phase.node_key}: {phase.status}
                      {phase.attempt && phase.attempt > 1 ? `×${phase.attempt}` : ''}
                    </span>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
