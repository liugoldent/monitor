import { useCallback, useEffect, useState } from 'react'
import type { SignalOverview } from '../types'

const apiBaseUrl = (import.meta.env.VITE_SIGNALOPS_API_URL as string | undefined)?.replace(/\/$/, '') ?? ''

type OverviewStatus =
  | { state: 'loading' }
  | { state: 'ready'; data: SignalOverview }
  | { state: 'error'; message: string }

export function useOverview() {
  const [status, setStatus] = useState<OverviewStatus>({ state: 'loading' })
  const [version, setVersion] = useState(0)
  const reload = useCallback(() => setVersion((value) => value + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    setStatus({ state: 'loading' })
    fetch(`${apiBaseUrl}/api/v1/overview`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`總覽 API 回應失敗（${response.status}）`)
        setStatus({ state: 'ready', data: (await response.json()) as SignalOverview })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setStatus({
          state: 'error',
          message: error instanceof Error ? error.message : '無法載入策略總覽',
        })
      })
    return () => controller.abort()
  }, [version])

  return { status, reload }
}
