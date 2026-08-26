import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { SignalEvent } from '../types'
import { SignalCard } from './SignalCard'

const event: SignalEvent = {
  id: '00000000-0000-0000-0000-000000000001',
  schema_version: 1,
  occurred_at: '2026-08-26T03:40:10Z',
  received_at: '2026-08-26T03:40:11Z',
  source: 'legacy_csv',
  instrument: 'MXF',
  strategy_code: 'TEST',
  strategy_name: '測試策略',
  account_ref: 'acct_1234567890abcdef',
  previous_position: 0,
  new_position: 1,
  action: 'enter',
  side: 'bull',
  quantity: '1',
  reference_price: null,
  attributes: {},
}

describe('SignalCard', () => {
  it('以中文呈現持倉轉換且不顯示匿名帳號代碼', () => {
    render(<SignalCard event={event} />)

    expect(screen.getByText('測試策略')).toBeInTheDocument()
    expect(screen.getByText('進場')).toBeInTheDocument()
    expect(screen.getByLabelText('持倉轉換')).toHaveTextContent('空手→多單')
    expect(screen.queryByText(event.account_ref as string)).not.toBeInTheDocument()
  })
})
