import { useCallback, useEffect, useState } from 'react'
import type {
  LiveConnectionStatus,
  SignalDataStatus,
  SignalEvent,
  SignalEventPage,
  SignalFilter,
} from '../types'

const apiBaseUrl = (import.meta.env.VITE_SIGNALOPS_API_URL as string | undefined)?.replace(/\/$/, '') ?? ''

function buildSignalsUrl(filter: SignalFilter, cursor?: string) {
  const parameters = new URLSearchParams({ limit: '24' })
  if (filter !== 'all') parameters.set('action', filter)
  if (cursor) parameters.set('cursor', cursor)
  return `${apiBaseUrl}/api/v1/signals?${parameters}`
}

async function fetchSignalPage(filter: SignalFilter, signal: AbortSignal, cursor?: string) {
  const response = await fetch(buildSignalsUrl(filter, cursor), { signal })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    const message =
      typeof detail?.detail === 'string' ? detail.detail : `事件 API 回應失敗（${response.status}）`
    throw new Error(message)
  }
  return (await response.json()) as SignalEventPage
}

export function useSignals(filter: SignalFilter) {
  const [status, setStatus] = useState<SignalDataStatus>({ state: 'loading', items: [] })
  const [liveStatus, setLiveStatus] = useState<LiveConnectionStatus>('connecting')
  const [requestVersion, setRequestVersion] = useState(0)

  const reload = useCallback(() => {
    setRequestVersion((version) => version + 1)
    setStatus({ state: 'loading', items: [] })
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchSignalPage(filter, controller.signal)
      .then((page) => {
        setStatus({
          state: 'ready',
          items: page.items,
          nextCursor: page.next_cursor,
          loadingMore: false,
        })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setStatus({
          state: 'error',
          items: [],
          message: error instanceof Error ? error.message : '無法載入策略事件',
        })
      })

    return () => controller.abort()
  }, [filter, requestVersion])

  const loadMore = useCallback(() => {
    if (status.state !== 'ready' || !status.nextCursor || status.loadingMore) return
    const controller = new AbortController()
    const currentItems = status.items
    const cursor = status.nextCursor
    setStatus({ ...status, loadingMore: true })

    fetchSignalPage(filter, controller.signal, cursor)
      .then((page) => {
        setStatus({
          state: 'ready',
          items: [...currentItems, ...page.items],
          nextCursor: page.next_cursor,
          loadingMore: false,
        })
      })
      .catch((error: unknown) => {
        setStatus({
          state: 'error',
          items: currentItems,
          message: error instanceof Error ? error.message : '無法載入較早事件',
        })
      })
  }, [filter, status])

  useEffect(() => {
    let reconnectTimer: number | undefined
    let socket: WebSocket | undefined
    let closed = false

    function connect() {
      setLiveStatus('connecting')
      const websocketBase = apiBaseUrl
        ? apiBaseUrl.replace(/^http/, 'ws')
        : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
      socket = new WebSocket(`${websocketBase}/api/v1/stream`)
      socket.addEventListener('open', () => setLiveStatus('connected'))
      socket.addEventListener('message', (message) => {
        const payload = JSON.parse(message.data as string) as {
          type: string
          data?: SignalEvent
        }
        if (payload.type !== 'signal_event' || !payload.data) return
        if (filter !== 'all' && payload.data.action !== filter) return
        setStatus((current) => {
          if (current.state !== 'ready') return current
          if (current.items.some((item) => item.id === payload.data?.id)) return current
          return { ...current, items: [payload.data as SignalEvent, ...current.items] }
        })
      })
      socket.addEventListener('close', () => {
        setLiveStatus('disconnected')
        if (!closed) reconnectTimer = window.setTimeout(connect, 3000)
      })
      socket.addEventListener('error', () => socket?.close())
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [filter])

  return { status, liveStatus, loadMore, reload }
}
