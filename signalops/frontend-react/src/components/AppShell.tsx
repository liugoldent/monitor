import { useLayoutEffect, useState, type ReactNode } from 'react'

type AppShellProps = {
  route: string
  onRouteChange: (route: string) => void
  children: ReactNode
}

const routes = [
  { index: '01', key: 'overview', label: '策略總覽' },
  { index: '02', key: 'analytics', label: '營運分析' },
  { index: '03', key: 'signals', label: '事件時間軸' },
  { index: '04', key: 'assistant', label: '策略小助手' },
  { index: '05', key: 'market', label: '市場資料' },
  { index: '06', key: 'react-notes', label: '工程筆記' },
]

type Theme = 'light' | 'dark'

function readInitialTheme(): Theme {
  try {
    const savedTheme = window.localStorage.getItem('signalops-theme')
    if (savedTheme === 'light' || savedTheme === 'dark') return savedTheme
  } catch {
    // 無法使用 localStorage 時，仍可依系統外觀正常顯示。
  }

  return typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light'
}

export function AppShell({ route, onRouteChange, children }: AppShellProps) {
  const [theme, setTheme] = useState<Theme>(readInitialTheme)

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
    try {
      window.localStorage.setItem('signalops-theme', theme)
    } catch {
      // 隱私模式封鎖儲存時，只保留當次瀏覽的切換結果。
    }
  }, [theme])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#/overview" onClick={() => onRouteChange('overview')}>
          <span className="brand-mark" aria-hidden="true">信</span>
          <span>
            <strong>SignalOps</strong>
            <small>STRATEGY INTELLIGENCE</small>
          </span>
        </a>

        <nav className="nav-list" aria-label="主要導覽">
          {routes.map((item) => (
            <a
              aria-current={route === item.key ? 'page' : undefined}
              className={route === item.key ? 'nav-item active' : 'nav-item'}
              href={`#/${item.key}`}
              key={item.key}
              onClick={() => onRouteChange(item.key)}
            >
              <span className="nav-index">{item.index}</span>
              <span>{item.label}</span>
            </a>
          ))}
        </nav>

        <div className="sidebar-footer">
          <button
            aria-label={`切換為${theme === 'dark' ? '日間' : '夜間'}模式`}
            aria-pressed={theme === 'dark'}
            className="theme-toggle"
            onClick={() => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))}
            type="button"
          >
            <span className="theme-symbol" aria-hidden="true">
              {theme === 'dark' ? '月' : '日'}
            </span>
            <span>
              <strong>{theme === 'dark' ? '夜間模式' : '日間模式'}</strong>
              <small>點擊切換外觀</small>
            </span>
          </button>
          <p>READ ONLY · TAIPEI UTC+8</p>
        </div>
      </aside>

      <main className="main-view">{children}</main>
    </div>
  )
}
