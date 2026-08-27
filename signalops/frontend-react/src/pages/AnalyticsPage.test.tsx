import { render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { useAnalytics } from '../hooks/useAnalytics'
import { AnalyticsPage } from './AnalyticsPage'

vi.mock('../hooks/useAnalytics', () => ({ useAnalytics: vi.fn() }))

test('分開呈現 ESBI 的 B 象限營運與 I 象限投資門檻', () => {
  vi.mocked(useAnalytics).mockReturnValue({
    status: {
      state: 'ready',
      data: {
        generated_at: '2026-08-26T00:00:00Z',
        periods: 12,
        kpis: {
          active_strategies: 6,
          exposure_rate: 0.5,
          reversal_rate: 0.1,
          average_events_per_strategy: 60.4,
          reference_price_coverage: 0,
        },
        activity: [
          { period: '2026-08', total: 10, entries: 4, exits: 4, reversals: 2 },
        ],
        transitions: [
          { previous_position: 0, new_position: 1, count: 4 },
        ],
        data_quality: {
          total_events: 725,
          missing_reference_price: 725,
          reference_price_coverage: 0,
          last_event_at: '2026-08-26T00:00:00Z',
        },
        limitations: ['目前不計算損益。'],
      },
    },
    reload: vi.fn(),
  })

  render(<AnalyticsPage />)

  expect(screen.getByRole('heading', { name: '從系統擁有者到投資者' })).toBeInTheDocument()
  expect(screen.getByText('BUSINESS OWNER · 大型企業／系統擁有者')).toBeInTheDocument()
  expect(screen.getByText('INVESTOR · 投資者')).toBeInTheDocument()
  expect(screen.getByText('50%')).toBeInTheDocument()
  expect(screen.getByText('I 象限尚未開放資本配置判斷')).toBeInTheDocument()
  expect(screen.queryByText(/Business Intelligence/i)).not.toBeInTheDocument()
})
