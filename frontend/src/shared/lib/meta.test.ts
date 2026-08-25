import { describe, expect, it } from 'vitest'

import {
  AI_NODE_KEYS,
  FINDING_ENGINE_LABELS,
  VERIFY_MODE_SKIPPED_KEYS,
  formatFindingEngines,
  formatTokenCount,
  getAiVerdictMeta,
  isAiNode,
  mergeTokenUsage,
} from './meta'

describe('AI_NODE_KEYS', () => {
  it('marks model-using pipeline nodes', () => {
    expect([...AI_NODE_KEYS].sort()).toEqual([
      'api_hunt',
      'audit',
      'env_ready',
      'lead_verify',
      'profile',
      'report',
      'reproduce',
      'screen',
      'triage',
    ])
    expect(isAiNode('api_hunt')).toBe(true)
    expect(isAiNode('lead_verify')).toBe(true)
    expect(isAiNode('cluster')).toBe(false)
  })
})

describe('VERIFY_MODE_SKIPPED_KEYS', () => {
  it('hides discovery-only nodes including inventory and hunt', () => {
    expect([...VERIFY_MODE_SKIPPED_KEYS].sort()).toEqual([
      'api_hunt',
      'api_inventory',
      'cluster',
      'dispatch',
      'scan_gitleaks',
      'scan_osv',
      'scan_semgrep',
      'screen',
      'triage',
    ])
  })
})

describe('mergeTokenUsage', () => {
  it('sums audit+reproduce style parts for lead_verify', () => {
    expect(mergeTokenUsage(
      {
        prompt_tokens: 10,
        completion_tokens: 2,
        cache_read_input_tokens: 100,
        cache_creation_input_tokens: 0,
        total_tokens: 112,
      },
      {
        prompt_tokens: 5,
        completion_tokens: 1,
        cache_read_input_tokens: 20,
        cache_creation_input_tokens: 3,
        total_tokens: 29,
      },
    )).toEqual({
      prompt_tokens: 15,
      completion_tokens: 3,
      cache_read_input_tokens: 120,
      cache_creation_input_tokens: 3,
      total_tokens: 141,
    })
  })
})

describe('FINDING_ENGINE_LABELS', () => {
  it('includes api_hunt alongside scan engines', () => {
    expect(Object.keys(FINDING_ENGINE_LABELS).sort()).toEqual([
      'api_hunt',
      'gitleaks',
      'osv',
      'semgrep',
    ])
    expect(formatFindingEngines(['api_hunt'])).toBe('API 鉴权猎洞')
    expect(formatFindingEngines(['semgrep', 'api_hunt'])).toBe('Semgrep 静态 + API 鉴权猎洞')
  })
})

describe('getAiVerdictMeta', () => {
  it('never shows raw tp/fp to users', () => {
    expect(getAiVerdictMeta('tp').label).toBe('可疑真洞')
    expect(getAiVerdictMeta('fp').label).toBe('误报')
    expect(getAiVerdictMeta('need_more_context').label).toBe('二审未决')
    for (const code of ['tp', 'fp', 'need_more_context', 'nope', null]) {
      const label = getAiVerdictMeta(code).label
      expect(label).not.toBe('tp')
      expect(label).not.toBe('fp')
    }
  })
})

describe('formatTokenCount', () => {
  it('formats compact magnitudes', () => {
    expect(formatTokenCount(0)).toBe('0')
    expect(formatTokenCount(42)).toBe('42')
    expect(formatTokenCount(1500)).toBe('1.5k')
    expect(formatTokenCount(1_400_000)).toBe('1.4M')
  })
})
