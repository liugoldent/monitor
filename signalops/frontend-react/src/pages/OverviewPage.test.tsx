import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OverviewPage } from './OverviewPage'

describe('OverviewPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('顯示由 API 回傳的策略總覽與目前持倉', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          generated_at: '2026-08-26T04:00:00Z',
          last_event_at: '2026-08-26T03:40:10Z',
          counts: {
            total_events: 725,
            strategies: 12,
            entries: 350,
            exits: 342,
            reversals: 33,
            long_positions: 5,
            short_positions: 1,
            flat_positions: 6,
          },
          positions: [
            {
              event_id: '00000000-0000-0000-0000-000000000001',
              strategy_code: 'TEST',
              strategy_name: '測試策略',
              instrument: 'MXF',
              position: 1,
              position_label: 'long',
              quantity: '1',
              updated_at: '2026-08-26T03:40:10Z',
            },
          ],
          strategies: [
            {
              strategy_code: 'TEST',
              strategy_name: '測試策略',
              event_count: 10,
              entries: 5,
              exits: 4,
              reversals: 1,
              current_position: 1,
              last_event_at: '2026-08-26T03:40:10Z',
            },
          ],
        }),
      }),
    )

    render(<OverviewPage />)

    expect(await screen.findByText('725')).toBeInTheDocument()
    expect(screen.getAllByText('測試策略')).toHaveLength(2)
    expect(screen.getAllByText('多單')).toHaveLength(2)
  })
})
