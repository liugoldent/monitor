import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { AppShell } from './AppShell'

describe('AppShell 外觀模式', () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
  })

  it('可在日間與夜間模式間切換並保存選擇', () => {
    window.localStorage.setItem('signalops-theme', 'light')

    render(
      <AppShell onRouteChange={() => undefined} route="overview">
        <div>測試內容</div>
      </AppShell>,
    )

    const toggle = screen.getByRole('button', { name: '切換為夜間模式' })
    expect(document.documentElement.dataset.theme).toBe('light')

    fireEvent.click(toggle)

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem('signalops-theme')).toBe('dark')
    expect(screen.getByRole('button', { name: '切換為日間模式' })).toBeInTheDocument()
  })
})
