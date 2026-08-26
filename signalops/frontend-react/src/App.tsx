import { useEffect, useState } from 'react'
import { AppShell } from './components/AppShell'
import { AssistantPage } from './pages/AssistantPage'
import { AnalyticsPage } from './pages/AnalyticsPage'
import { MarketPage } from './pages/MarketPage'
import { OverviewPage } from './pages/OverviewPage'
import { ReactNotesPage } from './pages/ReactNotesPage'
import { SignalsPage } from './pages/SignalsPage'

const routeKeys = new Set(['overview', 'analytics', 'signals', 'assistant', 'market', 'react-notes'])

function readRoute() {
  const route = window.location.hash.replace(/^#\/?/, '').split('?')[0] || 'overview'
  return routeKeys.has(route) ? route : 'overview'
}

function App() {
  const [route, setRoute] = useState(readRoute)

  useEffect(() => {
    function handleHashChange() {
      setRoute(readRoute())
    }

    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  function handleRouteChange(nextRoute: string) {
    if (!routeKeys.has(nextRoute)) return
    setRoute(nextRoute)
  }

  return (
    <AppShell onRouteChange={handleRouteChange} route={route}>
      {route === 'overview' && <OverviewPage />}
      {route === 'analytics' && <AnalyticsPage />}
      {route === 'signals' && <SignalsPage />}
      {route === 'assistant' && <AssistantPage />}
      {route === 'market' && <MarketPage />}
      {route === 'react-notes' && <ReactNotesPage />}
    </AppShell>
  )
}

export default App
