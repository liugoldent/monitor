import type { ReactNode } from 'react'

type AppShellProps = {
  route: string
  onRouteChange: (route: string) => void
  children: ReactNode
}

const routes = [
  { key: 'market', label: 'Market' },
  { key: 'react-notes', label: 'React Notes' },
]

export function AppShell({ route, onRouteChange, children }: AppShellProps) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#/market" onClick={() => onRouteChange('market')}>
          <span className="brand-mark">R</span>
          <span>
            <strong>React Futures</strong>
            <small>Binance Interview Lab</small>
          </span>
        </a>

        <nav className="nav-list" aria-label="Main navigation">
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
