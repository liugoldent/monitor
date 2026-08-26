import { useCallback, useRef, useState } from 'react'
import type { AssistantCitation, AssistantStatus } from '../types'

const apiBaseUrl = (import.meta.env.VITE_SIGNALOPS_API_URL as string | undefined)?.replace(/\/$/, '') ?? ''

type SseMessage = {
  event: string
  data: unknown
}

export function parseSseBlock(block: string): SseMessage | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trim())
  }
  if (data.length === 0) return null
  return { event, data: JSON.parse(data.join('\n')) as unknown }
}

export function useAssistant() {
  const [status, setStatus] = useState<AssistantStatus>({
    state: 'idle',
    text: '',
    citations: [],
    mode: null,
  })
  const activeController = useRef<AbortController | null>(null)

  const ask = useCallback(async (question: string) => {
    activeController.current?.abort()
    const controller = new AbortController()
    activeController.current = controller
    let text = ''
    let citations: AssistantCitation[] = []
    let mode: string | null = null
    setStatus({ state: 'streaming', text, citations, mode })

    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/assistant/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
        signal: controller.signal,
      })
      if (!response.ok || !response.body) {
        throw new Error(`小助手 API 回應失敗（${response.status}）`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const result = await reader.read()
        if (result.done) break
        buffer += decoder.decode(result.value, { stream: true }).replace(/\r\n/g, '\n')
        let separator = buffer.indexOf('\n\n')
        while (separator >= 0) {
          const message = parseSseBlock(buffer.slice(0, separator))
          buffer = buffer.slice(separator + 2)
          if (message?.event === 'meta') mode = (message.data as { mode: string }).mode
          if (message?.event === 'delta') text += (message.data as { text: string }).text
          if (message?.event === 'citations') citations = message.data as AssistantCitation[]
          setStatus({ state: 'streaming', text, citations, mode })
          separator = buffer.indexOf('\n\n')
        }
      }
      setStatus({ state: 'ready', text, citations, mode })
    } catch (error) {
      if (controller.signal.aborted) return
      setStatus({
        state: 'error',
        text,
        citations,
        mode,
        message: error instanceof Error ? error.message : '小助手暫時無法回答',
      })
    }
  }, [])

  return { status, ask }
}
