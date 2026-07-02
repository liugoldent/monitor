import { useEffect, useState } from 'react'
import type { DataStatus } from '../types'
import { parseWebhookCsv } from '../utils/csv'

const dataUrl = `${import.meta.env.BASE_URL}data/webhook_data_1min.csv`

export function useCandles(): DataStatus {
  const [status, setStatus] = useState<DataStatus>({ state: 'loading' })

  useEffect(() => {
    const controller = new AbortController()

    async function loadCandles() {
      try {
        const response = await fetch(dataUrl, { signal: controller.signal })

        if (!response.ok) {
          throw new Error(`CSV 載入失敗：${response.status}`)
        }

        const csv = await response.text()
        const candles = parseWebhookCsv(csv)
        setStatus({ state: 'ready', candles })
      } catch (error) {
        if (controller.signal.aborted) return
        const message = error instanceof Error ? error.message : 'CSV 載入失敗'
        setStatus({ state: 'error', message })
      }
    }

    loadCandles()

    return () => {
      controller.abort()
    }
  }, [])

  return status
}
