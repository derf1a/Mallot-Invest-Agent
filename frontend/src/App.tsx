import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './app/Layout'

// 화면마다 처음 열 때 불러온다 — 첫 화면 번들을 작게
const Invest = lazy(() => import('./features/invest/InvestPage'))
const Portfolio = lazy(() => import('./features/invest/PortfolioPage'))
const AiTrading = lazy(() => import('./features/invest/AiTradingPage'))
const Team = lazy(() => import('./features/invest/TeamPage'))
const Settings = lazy(() => import('./features/settings/SettingsPage'))
const Agents = lazy(() => import('./features/observe/AgentsPage'))
const Graph = lazy(() => import('./features/observe/GraphPage'))
const Traces = lazy(() => import('./features/observe/TracesPage'))

const page = (el: ReactNode) => <Suspense fallback={<p className="muted">불러오는 중…</p>}>{el}</Suspense>

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/invest/ai" replace />} />
        <Route path="invest" element={page(<Invest />)} />
        <Route path="invest/portfolio" element={page(<Portfolio />)} />
        <Route path="invest/ai" element={page(<AiTrading />)} />
        <Route path="invest/team" element={page(<Team />)} />
        <Route path="observe/agents" element={page(<Agents />)} />
        <Route path="observe/graph" element={page(<Graph />)} />
        <Route path="observe/traces" element={page(<Traces />)} />
        <Route path="settings" element={page(<Settings />)} />
        <Route path="*" element={<Navigate to="/invest/ai" replace />} />
      </Route>
    </Routes>
  )
}
