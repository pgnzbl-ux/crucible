import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { shouldRetryQuery } from '../shared/lib/queryRetry'
import { crucibleTheme } from '../styles/theme'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: shouldRetryQuery,
    },
  },
})

export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={crucibleTheme}>
        {children}
      </ConfigProvider>
    </QueryClientProvider>
  )
}
