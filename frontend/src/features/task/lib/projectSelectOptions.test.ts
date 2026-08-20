import { describe, expect, it } from 'vitest'

import {
  buildGitProjectOptions,
  classifyProjectRef,
  filterGitProjectOption,
  formatProjectVersionLabel,
} from './projectSelectOptions'

const BASE = {
  id: 'p1',
  name: '禅道',
  git_url: 'https://github.com/easysoft/zentaopms',
  default_ref: null as string | null,
}

describe('formatProjectVersionLabel', () => {
  it('拼成 名称：类型/引用  <地址>', () => {
    expect(
      formatProjectVersionLabel('禅道', 'tag', 'zentaopms_22.4_20260730', BASE.git_url),
    ).toBe('禅道：tag/zentaopms_22.4_20260730  <https://github.com/easysoft/zentaopms>')
  })
})

describe('classifyProjectRef', () => {
  it.each([
    [null, { ref_type: 'branch', ref_name: 'HEAD' }],
    ['', { ref_type: 'branch', ref_name: 'HEAD' }],
    ['main', { ref_type: 'branch', ref_name: 'main' }],
    ['v1.2.0', { ref_type: 'tag', ref_name: 'v1.2.0' }],
    ['zentaopms_22.4_20260730', { ref_type: 'tag', ref_name: 'zentaopms_22.4_20260730' }],
    ['abcdef1', { ref_type: 'commit', ref_name: 'abcdef1' }],
  ] as const)('classify %j', (input, want) => {
    expect(classifyProjectRef(input)).toEqual(want)
  })
})

describe('buildGitProjectOptions', () => {
  it('无制品时用默认引用推断类型', () => {
    const options = buildGitProjectOptions([
      { ...BASE, default_ref: 'zentaopms_22.4_20260730' },
    ])
    expect(options).toHaveLength(1)
    expect(options[0].label).toBe(
      '禅道：tag/zentaopms_22.4_20260730  <https://github.com/easysoft/zentaopms>',
    )
    expect(options[0]).toMatchObject({
      git_url: BASE.git_url,
      ref_type: 'tag',
      ref_name: 'zentaopms_22.4_20260730',
    })
  })

  it('有缓存制品时每个版本一行', () => {
    const options = buildGitProjectOptions([
      {
        ...BASE,
        name: 'claudecodeui',
        git_url: 'https://github.com/siteboon/claudecodeui',
        source_refs: [
          { ref_type: 'branch', ref_name: 'main' },
          { ref_type: 'tag', ref_name: 'v1.2.0' },
        ],
      },
    ])
    expect(options.map((o) => o.label)).toEqual([
      'claudecodeui：branch/main  <https://github.com/siteboon/claudecodeui>',
      'claudecodeui：tag/v1.2.0  <https://github.com/siteboon/claudecodeui>',
    ])
  })

  it('HEAD 不预填引用名称，避免挡住默认分支推断', () => {
    const [option] = buildGitProjectOptions([BASE])
    expect(option.label).toBe('禅道：branch/HEAD  <https://github.com/easysoft/zentaopms>')
    expect(option.ref_name).toBeUndefined()
  })
})

describe('filterGitProjectOption', () => {
  const option = {
    label: '禅道：tag/v22.4  <https://github.com/easysoft/zentaopms>',
    git_url: 'https://github.com/easysoft/zentaopms',
  }

  it.each([
    ['禅道', true],
    ['tag/v22.4', true],
    ['easysoft', true],
    ['not-this-repo', false],
  ])('input %s → %s', (input, want) => {
    expect(filterGitProjectOption(input, option)).toBe(want)
  })
})
