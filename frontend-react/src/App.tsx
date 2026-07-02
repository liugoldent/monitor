import { useEffect, useState } from 'react'
import { AppShell } from './components/AppShell'
import { MarketPage } from './pages/MarketPage'
import { ReactNotesPage } from './pages/ReactNotesPage'

const routeKeys = new Set(['market', 'react-notes'])

function readRoute() {
  const route = window.location.hash.replace(/^#\/?/, '') || 'market'
  return routeKeys.has(route) ? route : 'market'
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
      {route === 'react-notes' ? <ReactNotesPage /> : <MarketPage />}
    </AppShell>
  )
}

export default App
