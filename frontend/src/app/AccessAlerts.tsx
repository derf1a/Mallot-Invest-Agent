/** 관리자 화면 위 띠 — 확인하지 않은 한국 밖 접속 경보가 있으면 보여 준다 (화면을 옮길 때 · 1분마다 다시 본다) */
import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { authApi, type AccessAlert } from '../features/auth/api'

export default function AccessAlerts() {
  const location = useLocation()
  const [alerts, setAlerts] = useState<AccessAlert[]>([])
  useEffect(() => {
    let alive = true
    const load = () => authApi.alerts().then((a) => { if (alive) setAlerts(a) }).catch(() => {})
    load()
    const timer = window.setInterval(load, 60_000)
    window.addEventListener('access-alerts-changed', load)
    return () => { alive = false; window.clearInterval(timer); window.removeEventListener('access-alerts-changed', load) }
  }, [location.pathname])
  if (!alerts.length) return null
  const a = alerts[0]
  return (
    <div className="access-banner" role="alert">
      <b>⚠ 한국 밖 접속 {alerts.length}건</b>
      <span>{a.country ?? '?'} · <span className="mono">{a.ip}</span> · {a.reason}</span>
      <Link to="/settings?tab=access">접속 기록 →</Link>
      <button className="small" onClick={() => authApi.ack().then(() => setAlerts([]))}>모두 확인</button>
    </div>
  )
}
