import type { KeyboardEvent } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { tableRowNavigateProps } from './tableRowNavigate'

describe('tableRowNavigateProps', () => {
  it('opens on click', () => {
    const go = vi.fn()
    const props = tableRowNavigateProps(go)
    props.onClick()
    expect(go).toHaveBeenCalledTimes(1)
  })

  it('opens on Enter and Space for keyboard users', () => {
    const go = vi.fn()
    const props = tableRowNavigateProps(go)
    props.onKeyDown({ key: 'Enter', preventDefault: vi.fn() } as unknown as KeyboardEvent)
    props.onKeyDown({ key: ' ', preventDefault: vi.fn() } as unknown as KeyboardEvent)
    props.onKeyDown({ key: 'Tab', preventDefault: vi.fn() } as unknown as KeyboardEvent)
    expect(go).toHaveBeenCalledTimes(2)
    expect(props.tabIndex).toBe(0)
    expect(props.role).toBe('link')
  })
})
