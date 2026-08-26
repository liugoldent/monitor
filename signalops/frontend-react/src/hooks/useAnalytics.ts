import { useCallback, useEffect, useState } from 'react'
import type { BusinessAnalytics } from '../types'

const apiBaseUrl = (import.meta.env.VITE_SIGNALOPS_API_URL as string | undefined)?.replace(/\/$/, '') ?? ''

type AnalyticsStatus =
  | { state: 'loading' }
  | { state: 'ready'; data: BusinessAnalytics }
  | { state: 'error'; message: string }

export function useAnalytics(periods = 12) {
  const [status, setStatus] = useState<AnalyticsStatus>({ state: 'loading' })
  const [version, setVersion] = useState(0)
  const reload = useCallback(() => setVersion((value) => value + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    setStatus({ state: 'loading' })
    fetch(`${apiBaseUrl}/api/v1/analytics?periods=${periods}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`營運分析 API 回應失敗（${response.status}）`)
        setStatus({ state: 'ready', data: (await response.json()) as BusinessAnalytics })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setStatus({
          state: 'error',
          message: error instanceof Error ? error.message : '無法載入營運分析',
        })
      })
    return () => controller.abort()
  }, [periods, version])

  return { status, reload }
}
