import { ApiError } from './api'

const RETRYABLE_CLIENT_STATUS = new Set([408, 429])

/** 4xx 除超时/限流外不重试；401 已在 request 里清会话。 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (
    error instanceof ApiError &&
    error.status >= 400 &&
    error.status < 500 &&
    !RETRYABLE_CLIENT_STATUS.has(error.status)
  ) {
    return false
  }
  return failureCount < 2
}
