/**
 * 把 audit 节点的 output 归一化成可排版的结构。
 *
 * Agent 交回来的 gate_reason 是"Q1…Q2…Q3…"压成的一整段，kill_chain 是箭头连起来的超长单行，
 * defense_layers / payloads 的键名还各写各的。这里统一拆开，展示层才能分区渲染。
 */

export type GateVerdict = 'pass' | 'fail' | 'uncertain' | 'unknown'

export type GateQuestionKey = 'q1' | 'q2' | 'q3' | 'runtime'

export interface AuditGateQuestion {
  key: GateQuestionKey
  label: string
  text: string
}

export interface AuditDefenseLayer {
  layer: string
  bypass: string
}

export interface AuditPayloadItem {
  request: string
  expectation: string
}

export interface AuditView {
  verdict: GateVerdict
  runtimeDependent: boolean
  /** gate_reason 原文，拆分失败时兜底展示 */
  gateReason: string
  /** 三问之前的引导语 */
  gateReasonLead: string
  questions: AuditGateQuestion[]
  killChainSteps: string[]
  defenseLayers: AuditDefenseLayer[]
  payloads: AuditPayloadItem[]
  hasStructuredDetail: boolean
}

const GATE_QUESTION_LABELS: Record<GateQuestionKey, string> = {
  q1: 'Q1 · 核心主张',
  q2: 'Q2 · 链路连通',
  q3: 'Q3 · 结构性阻断',
  runtime: '运行时依赖',
}

const GATE_VERDICTS: GateVerdict[] = ['pass', 'fail', 'uncertain']

/**
 * skill 常见写法：`Q1:`、`Q1(核心主张):`、`Q1 核心主张：`、`**Q1 核心主张**：`、`运行时依赖：`
 */
const GATE_MARKER =
  /(?:\*\*)?(Q[123])\s*(?:\*\*)?(?:\s*[（(][^)）]*[)）])?(?:\s*(?:核心主张|链路连通|结构性阻断))?(?:\*\*)?\s*[:：]|(?:runtime[-\s]?dependent|运行时依赖)\s*[:：]/gi

/** 箭头与换行都当步骤分隔 */
const CHAIN_SEPARATOR = /\s*(?:-{1,2}>|=>|→|\n)\s*/

function str(v: unknown): string {
  return typeof v === 'string' ? v.trim() : ''
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v)
}

export function splitGateReason(reason: string): { lead: string; questions: AuditGateQuestion[] } {
  const text = str(reason)
  if (!text) return { lead: '', questions: [] }

  const marks: Array<{ key: GateQuestionKey; start: number; end: number }> = []
  for (const m of text.matchAll(GATE_MARKER)) {
    const start = m.index ?? 0
    marks.push({
      key: m[1] ? (m[1].toLowerCase() as GateQuestionKey) : 'runtime',
      start,
      end: start + m[0].length,
    })
  }
  if (!marks.length) return { lead: text, questions: [] }

  const questions = marks.map((mark, i) => ({
    key: mark.key,
    label: GATE_QUESTION_LABELS[mark.key],
    text: text.slice(mark.end, i + 1 < marks.length ? marks[i + 1].start : text.length).trim(),
  }))
  return { lead: text.slice(0, marks[0].start).trim(), questions }
}

function splitNumberedSteps(text: string): string[] {
  const parts = text.split(/(?=\d+\.\s+)/).map((step) => step.replace(/^\d+\.\s+/, '').trim()).filter(Boolean)
  return parts.length > 1 ? parts : []
}

export function splitKillChain(chain: string): string[] {
  const text = str(chain)
  if (!text) return []

  let steps = text
    .split(CHAIN_SEPARATOR)
    .map((step) => step.trim())
    .filter(Boolean)

  if (steps.length === 1) {
    const numbered = splitNumberedSteps(steps[0])
    if (numbered.length) steps = numbered
  }

  return steps
}

function normalizeDefenseLayers(raw: unknown): AuditDefenseLayer[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item) => {
    if (typeof item === 'string') {
      const layer = item.trim()
      return layer ? [{ layer, bypass: '' }] : []
    }
    if (!isRecord(item)) return []
    const layer = str(item.layer) || str(item.name) || str(item.title)
    const bypass = str(item.bypass) || str(item.bypassed) || str(item.status)
    return layer || bypass ? [{ layer, bypass }] : []
  })
}

function normalizePayloads(raw: unknown): AuditPayloadItem[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item) => {
    if (typeof item === 'string') {
      const request = item.trim()
      return request ? [{ request, expectation: '' }] : []
    }
    if (!isRecord(item)) return []
    const request = str(item.request) || str(item.payload) || str(item.poc) || str(item.curl)
    const expectation = str(item.expectation) || str(item.expected) || str(item.effect)
    return request || expectation ? [{ request, expectation }] : []
  })
}

export function parseAuditOutput(output: Record<string, unknown> | null | undefined): AuditView {
  const o = isRecord(output) ? output : {}
  const rawVerdict = str(o.gate_verdict) as GateVerdict
  const gateReason = str(o.gate_reason)
  const { lead, questions } = splitGateReason(gateReason)
  const killChainSteps = splitKillChain(str(o.kill_chain))
  const defenseLayers = normalizeDefenseLayers(o.defense_layers)
  const payloads = normalizePayloads(o.payloads)

  return {
    verdict: GATE_VERDICTS.includes(rawVerdict) ? rawVerdict : 'unknown',
    runtimeDependent: o.runtime_dependent === true,
    gateReason,
    gateReasonLead: lead,
    questions,
    killChainSteps,
    defenseLayers,
    payloads,
    hasStructuredDetail: Boolean(
      gateReason || killChainSteps.length || defenseLayers.length || payloads.length,
    ),
  }
}
