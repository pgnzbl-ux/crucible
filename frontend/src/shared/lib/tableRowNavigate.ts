import type { KeyboardEvent } from 'react'

export function tableRowNavigateProps(go: () => void) {
  return {
    role: 'link' as const,
    tabIndex: 0,
    style: { cursor: 'pointer' as const },
    onClick: () => go(),
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key !== 'Enter' && event.key !== ' ') return
      event.preventDefault()
      go()
    },
  }
}
