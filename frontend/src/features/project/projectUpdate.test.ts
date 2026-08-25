import { describe, expect, it } from 'vitest'

import type { Project } from '../../shared/lib/api'
import {
  buildProjectUpdatePayload,
  isUploadProject,
  projectEditInitialValues,
} from './projectUpdate'

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    name: 'claudecodeui',
    git_url: 'https://github.com/siteboon/claudecodeui',
    source_type: 'git',
    default_ref: 'main',
    default_ref_type: 'branch',
    description: '桌面客户端',
    owner_id: 'u1',
    detected_language: 'TypeScript',
    detected_framework: 'React',
    is_web: true,
    last_cloned_at: null,
    created_at: '',
    updated_at: '',
    ...overrides,
  }
}

describe('isUploadProject', () => {
  it('区分 Git 与本地上传', () => {
    expect(isUploadProject(project())).toBe(false)
    expect(isUploadProject(project({ source_type: 'local_upload' }))).toBe(true)
  })
})

describe('projectEditInitialValues', () => {
  it('Git 项目带出名称、备注和默认引用', () => {
    expect(projectEditInitialValues(project())).toEqual({
      name: 'claudecodeui',
      description: '桌面客户端',
      default_ref_type: 'branch',
      default_ref: 'main',
    })
  })

  it('分支 HEAD / 空引用在表单里留空', () => {
    expect(projectEditInitialValues(project({ default_ref: 'HEAD', default_ref_type: 'branch' })).default_ref).toBe(
      '',
    )
    expect(projectEditInitialValues(project({ default_ref: null, default_ref_type: 'branch' })).default_ref).toBe('')
  })

  it('未写 default_ref_type 时按引用名称推断', () => {
    const values = projectEditInitialValues(
      project({ default_ref: 'v1.2.0', default_ref_type: null }),
    )
    expect(values.default_ref_type).toBe('tag')
    expect(values.default_ref).toBe('v1.2.0')
  })

  it('上传项目只回填名称和备注', () => {
    expect(
      projectEditInitialValues(
        project({
          source_type: 'local_upload',
          git_url: 'upload://local/p1',
          default_ref: 'local',
          default_ref_type: null,
          description: null,
        }),
      ),
    ).toEqual({
      name: 'claudecodeui',
      description: '',
    })
  })
})

describe('buildProjectUpdatePayload', () => {
  it.each([
    {
      name: 'Git 提交全部可编辑字段',
      source_type: 'git' as const,
      values: {
        name: '  new-name ',
        description: '  备注 ',
        default_ref_type: 'tag' as const,
        default_ref: ' v2.0.0 ',
      },
      want: {
        name: 'new-name',
        description: '备注',
        default_ref_type: 'tag',
        default_ref: 'v2.0.0',
      },
    },
    {
      name: '清空默认引用时仍提交空字符串',
      source_type: 'git' as const,
      values: { name: 'app', description: '', default_ref_type: 'branch' as const, default_ref: '' },
      want: {
        name: 'app',
        description: '',
        default_ref_type: 'branch',
        default_ref: '',
      },
    },
    {
      name: '上传项目不提交默认引用',
      source_type: 'local_upload' as const,
      values: {
        name: 'demo',
        description: '说明',
        default_ref_type: 'branch' as const,
        default_ref: 'main',
      },
      want: { name: 'demo', description: '说明' },
    },
  ])('$name', ({ source_type, values, want }) => {
    expect(buildProjectUpdatePayload({ source_type }, values)).toEqual(want)
  })
})
