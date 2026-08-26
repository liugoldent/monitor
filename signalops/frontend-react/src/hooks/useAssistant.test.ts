import { describe, expect, it } from 'vitest'
import { parseSseBlock } from './useAssistant'

describe('parseSseBlock', () => {
  it('解析 SSE event 與 JSON data', () => {
    expect(parseSseBlock('event: delta\ndata: {"text":"持倉"}')).toEqual({
      event: 'delta',
      data: { text: '持倉' },
    })
  })

  it('忽略沒有 data 的 heartbeat block', () => {
    expect(parseSseBlock('event: heartbeat')).toBeNull()
  })
})
