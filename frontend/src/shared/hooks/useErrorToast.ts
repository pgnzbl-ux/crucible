import { useEffect, useRef } from 'react'
import { App } from 'antd'

import { nextErrorToast } from '../lib/errorToast'

/** 查询失败弹 toast；轮询同一错误不会连弹。操作失败请直接 message.error。 */
export function useErrorToast(isError: boolean, error: unknown, fallback = '请求失败') {
  const { message } = App.useApp()
  const lastText = useRef<string | null>(null)

  useEffect(() => {
    const next = nextErrorToast(lastText.current, isError, error, fallback)
    lastText.current = next.lastText
    if (next.toast) message.error(next.toast)
  }, [isError, error, fallback, message])
}
