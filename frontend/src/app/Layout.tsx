import { useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { systemApi } from '../features/settings/api'
import { useLoad } from '../shared/useLoad'
import BrandMark from './BrandMark'
import { AgentsIcon, AiIcon, GraphIcon, InvestIcon, PortfolioIcon, SettingsIcon, TeamIcon, TraceIcon } from './NavIcons'

interface NavItem {
  to: string
  label: string
  icon: ReactNode
}

interface NavSection {
  title: string
  items: NavItem[]
}

/** 메뉴 — 위는 매매, 아래는 에이전트를 들여다보는 화면 (Observe), 맨 아래 작게 설정 */
const SECTIONS: NavSection[] = [
  {
    title: 'Trade',
    items: [
      { to: '/invest/ai', label: 'AI Trading', icon: <AiIcon /> },
      { to: '/invest', label: 'Market', icon: <InvestIcon /> },
      { to: '/invest/portfolio', label: 'Portfolio', icon: <PortfolioIcon /> },
      { to: '/invest/team', label: 'Team', icon: <TeamIcon /> },
    ],
  },
  {
    title: 'Observe',
    items: [
      { to: '/observe/agents', label: 'Agents', icon: <AgentsIcon /> },
      { to: '/observe/graph', label: 'Graph', icon: <GraphIcon /> },
      { to: '/observe/traces', label: 'Traces', icon: <TraceIcon /> },
    ],
  },
]
const UTILITY: NavItem[] = [{ to: '/settings', label: 'Settings', icon: <SettingsIcon /> }]

function Wordmark() {
  return (
    <span className="wordmark">
      <span className="wm-m">M</span>
      <span className="wm-allot">allot</span>
      <span className="wm-sub">invest</span>
    </span>
  )
}

// PC: 왼쪽 사이드바 고정 / 모바일: 좌상단 햄버거 버튼으로 여닫는 드로어
export default function Layout() {
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()
  const { data: health } = useLoad(() => systemApi.health().catch(() => null), [])

  const all = [...SECTIONS.flatMap((s) => s.items), ...UTILITY]
  const current = all.find((item) => item.to === location.pathname)
  const link = (item: NavItem, kind: '' | 'small' = '') => (
    <NavLink key={item.to} to={item.to} end className={`nav-link${kind ? ` ${kind}` : ''}`} onClick={() => setMenuOpen(false)}>
      <span className="nav-icon">{item.icon}</span>
      <span>{item.label}</span>
    </NavLink>
  )

  return (
    <div className="app">
      <header className="topbar">
        <button className="menu-button" aria-label="메뉴 열기" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)}>
          ☰
        </button>
        <BrandMark size={24} />
        <span className="topbar-title">{current?.label ?? 'Mallot Invest'}</span>
      </header>

      {menuOpen && <div className="backdrop" onClick={() => setMenuOpen(false)} />}

      <nav className={`sidebar${menuOpen ? ' open' : ''}`}>
        <NavLink to="/invest/ai" className="brand" onClick={() => setMenuOpen(false)}>
          <BrandMark />
          <Wordmark />
        </NavLink>
        <div className="nav-main">
          {SECTIONS.map((s) => (
            <div key={s.title} className="nav-group">
              <span className="nav-section">{s.title}</span>
              {s.items.map((item) => link(item))}
            </div>
          ))}
        </div>
        <div className="nav-utility">{UTILITY.map((item) => link(item, 'small'))}</div>
        <footer className="sidebar-footer">
          <BrandMark size={18} />
          <span className="footer-text">
            <span className="footer-version">Mallot Invest v{__APP_VERSION__}</span>
            <span>
              build {__GIT_SHA__}
              {health && ` · db v${health.db_version}`}
            </span>
          </span>
        </footer>
      </nav>

      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
