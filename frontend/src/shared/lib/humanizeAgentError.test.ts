import { describe, expect, it } from 'vitest'

import { humanizeAgentError } from './humanizeAgentError'

describe('humanizeAgentError source workspace failures', () => {
  it('does not misclassify workspace permissions as Git credentials', () => {
    expect(humanizeAgentError('源码工作区准备失败: 无法清理 /tmp/audit/repo')).toMatchObject({
      title: '源码工作目录权限异常',
    })
  })

  it('recognizes the legacy non-empty clone destination error', () => {
    expect(
      humanizeAgentError(
        "fatal: destination path '/tmp/audit/repo' already exists and is not an empty directory",
      ),
    ).toMatchObject({ title: '源码工作目录没有清空' })
  })
})
