import type { ReactNode } from 'react'

type AppShellProps = {
  route: string
  onRouteChange: (route: string) => void
  children: ReactNode
}

const routes = [
  { key: 'overview', label: '策略總覽' },
  { key: 'analytics', label: '營運分析' },
  { key: 'signals', label: '事件時間軸' },
  { key: 'assistant', label: '策略小助手' },
  { key: 'market', label: '市場資料' },
  { key: 'react-notes', label: '工程筆記' },
]

export function AppShell({ route, onRouteChange, children }: AppShellProps) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#/overview" onClick={() => onRouteChange('overview')}>
          <span className="brand-mark">S</span>
          <span>
            <strong>SignalOps AI</strong>
            <small>策略事件情報平台</small>
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
              {item.label}
            </a>
          ))}
        </nav>
      </aside>

      <main className="main-view">{children}</main>
    </div>
  )
}
