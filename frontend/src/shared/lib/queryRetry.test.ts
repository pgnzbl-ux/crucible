import { describe, expect, it } from 'vitest'

import { ApiError } from './api'
import { shouldRetryQuery } from './queryRetry'

describe('shouldRetryQuery', () => {
  it.each([401, 403, 404, 409, 422])('does not retry %s', (status) => {
    expect(shouldRetryQuery(0, new ApiError('nope', status))).toBe(false)
  })

  it('retries 408/429 and 5xx up to twice', () => {
    expect(shouldRetryQuery(0, new ApiError('timeout', 408))).toBe(true)
    expect(shouldRetryQuery(0, new ApiError('slow down', 429))).toBe(true)
    expect(shouldRetryQuery(1, new ApiError('boom', 500))).toBe(true)
    expect(shouldRetryQuery(2, new ApiError('boom', 500))).toBe(false)
  })
})
