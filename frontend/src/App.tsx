import { lazy, Suspense, useCallback, useEffect, useState, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import Layout from './app/Layout'
import ViewerLayout from './app/ViewerLayout'
import { authApi, type Me } from './features/auth/api'
import LoginPage from './features/auth/LoginPage'
import { UNAUTHORIZED_EVENT } from './shared/http'

// 화면마다 처음 열 때 불러온다 — 첫 화면 번들을 작게
const Invest = lazy(() => import('./features/invest/InvestPage'))
const Portfolio = lazy(() => import('./features/invest/PortfolioPage'))
const AiTrading = lazy(() => import('./features/invest/AiTradingPage'))
const Team = lazy(() => import('./features/invest/TeamPage'))
const Settings = lazy(() => import('./features/settings/SettingsPage'))
const Agents = lazy(() => import('./features/observe/AgentsPage'))
const Graph = lazy(() => import('./features/observe/GraphPage'))
const Traces = lazy(() => import('./features/observe/TracesPage'))
const Share = lazy(() => import('./features/share/SharePage'))

const page = (el: ReactNode) => <Suspense fallback={<p className="muted">불러오는 중…</p>}>{el}</Suspense>

/**
 * 역할에 따라 다른 앱: 관리자 = 모든 화면, 손님 = 운용 현황(/share)만, 아무도 아님 = 공유 링크 입장 또는 관리자 로그인.
 * 로그인이 꺼진 모드(이 컴퓨터에서만 쓰기)에서는 서버가 언제나 관리자로 답한다.
 */
export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => authApi.me().then(setMe).catch((e: Error) => setError(e.message)), [])
  useEffect(() => { load() }, [load])
  // 쓰는 중에 로그인이 풀리면(7일 만료 · 비밀번호 변경 · 공유 키 교체) API 가 401 → 역할을 다시 읽어 로그인 화면으로
  useEffect(() => {
    const onUnauthorized = () => { load() }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [load])
  const logout = useCallback(() => authApi.logout().finally(load), [load])

  if (error) return <p className="error page-error">{error}</p>
  if (!me) return <p className="muted page-error">불러오는 중…</p>
  if (me.role === 'admin') return <AdminApp me={me} onLogout={logout} />
  if (me.role === 'viewer') return <ViewerLayout onLeave={logout} onLogin={() => setMe({ ...me, role: null })}>{page(<Share />)}</ViewerLayout>
  return <Gate me={me} onDone={load} />
}

function AdminApp({ me, onLogout }: { me: Me; onLogout: () => void }) {
  return (
    <Routes>
      <Route element={<Layout onLogout={me.auth ? onLogout : undefined} />}>
        <Route index element={<Navigate to="/invest/ai" replace />} />
        <Route path="invest" element={page(<Invest />)} />
        <Route path="invest/portfolio" element={page(<Portfolio />)} />
        <Route path="invest/ai" element={page(<AiTrading />)} />
        <Route path="invest/team" element={page(<Team />)} />
        <Route path="observe/agents" element={page(<Agents />)} />
        <Route path="observe/graph" element={page(<Graph />)} />
        <Route path="observe/traces" element={page(<Traces />)} />
        <Route path="share" element={page(<Share preview />)} />
        <Route path="settings" element={page(<Settings />)} />
        <Route path="*" element={<Navigate to="/invest/ai" replace />} />
      </Route>
    </Routes>
  )
}

/** 로그인 전: /share?k=… 이면 손님으로 들어가고, 아니면 관리자 로그인 */
function Gate({ me, onDone }: { me: Me; onDone: () => Promise<unknown> }) {
  const location = useLocation()
  const navigate = useNavigate()
  const key = location.pathname === '/share' ? new URLSearchParams(location.search).get('k') : null
  const [entering, setEntering] = useState(Boolean(key))
  const [failed, setFailed] = useState<string | null>(null)
  useEffect(() => {
    if (!key) return
    authApi.enter(key)
      .then(() => {
        // 주소창 · 라우터 둘 다에서 키를 지운다 — 남아 있으면 "관리자 로그인"을 눌러도 이 키로 다시 손님 입장한다
        navigate('/share', { replace: true })
        return onDone()
      })
      .catch((e: Error) => setFailed(e.message))
      .finally(() => setEntering(false))  // 손님이 되면 이 화면은 사라진다. 쿠키가 막혀 그대로면 로그인 화면으로
  }, [key, onDone, navigate])
  if (entering) return <p className="muted page-error">들어가는 중…</p>
  return (
    <>
      {failed && <p className="error page-error">{failed}</p>}
      <LoginPage me={me} onDone={onDone} />
    </>
  )
}
