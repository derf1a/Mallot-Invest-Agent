import { useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { investApi } from '../features/invest/api'
import { systemApi } from '../features/settings/api'
import { useLoad } from '../shared/useLoad'
import BrandMark from './BrandMark'
import { AgentsIcon, AiIcon, GraphIcon, InvestIcon, PortfolioIcon, SettingsIcon, TeamIcon, TraceIcon, TradesIcon } from './NavIcons'

interface NavItem {
  to: string
  label: string
  icon: ReactNode
  /** 오른쪽에 붙는 작은 상태 표시 (예: 자동 운용) */
  badge?: ReactNode
}

interface NavSection {
  title: string
  items: NavItem[]
}

/**
 * 메뉴 — 무엇을 하는 화면인지로 나눈다.
 * Operate: 에이전트에게 맡기는 운용(채팅방 · 팀), Invest: 내가 보는 시장 · 계좌 · 거래, Observe: 에이전트를 들여다보기. 맨 아래 작게 설정.
 */
function sections(pilot: ReactNode): NavSection[] {
  return [
    {
      title: 'Operate',
      items: [
        { to: '/invest/ai', label: 'AI Trading', icon: <AiIcon />, badge: pilot },
        { to: '/invest/team', label: 'Team', icon: <TeamIcon /> },
      ],
    },
    {
      title: 'Invest',
      items: [
        { to: '/invest', label: 'Market', icon: <InvestIcon /> },
        { to: '/invest/portfolio', label: 'Portfolio', icon: <PortfolioIcon /> },
        { to: '/invest/trades', label: 'Trades', icon: <TradesIcon /> },
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
}
const UTILITY: NavItem[] = [{ to: '/settings', label: 'Settings', icon: <SettingsIcon /> }]
const MODE_KO = { continuous: '상시', meetings: '회의' } as const

/** 자동 운용 켜짐 표시 — 꺼져 있으면 아무것도 붙이지 않는다 */
function PilotBadge({ on, mode }: { on: boolean; mode: keyof typeof MODE_KO }) {
  if (!on) return null
  const tip = `자동 운용 켜짐 · ${mode === 'continuous' ? '상시 운용' : '정기 회의'}`
  return (
    <span className="nav-badge" title={tip} aria-label={tip}>
      <span className="nav-badge-dot" />
      {MODE_KO[mode]}
    </span>
  )
}

function Wordmark() {
  return (
    <span className="wordmark">
      <span className="wm-m">M</span>
      <span className="wm-allot">allot</span>
      <span className="wm-sub">invest</span>
    </span>
  )
}

/** 손님(공유 링크)으로 볼 때 — 읽기 전용 표시와 관리자 로그인 · 나가기 */
export interface ViewerActions { onLogin: () => void; onLeave: () => void }

// PC: 왼쪽 사이드바 고정 / 모바일: 좌상단 햄버거 버튼으로 여닫는 드로어
export default function Layout({ onLogout, viewer }: { onLogout?: () => void; viewer?: ViewerActions }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()
  const { data: health } = useLoad(() => systemApi.health().catch(() => null), [])
  // 화면을 옮길 때마다 다시 본다 — 채팅으로 켜고 꺼도 다음 이동 때 맞춰진다
  const { data: pilot } = useLoad(() => investApi.autopilotSwitch().catch(() => null), [location.pathname])
  const SECTIONS = sections(pilot ? <PilotBadge on={pilot.enabled} mode={pilot.mode} /> : null)

  const all = [...SECTIONS.flatMap((s) => s.items), ...UTILITY]
  const current = all.find((item) => item.to === location.pathname)
  const link = (item: NavItem, kind: '' | 'small' = '') => (
    <NavLink key={item.to} to={item.to} end className={`nav-link${kind ? ` ${kind}` : ''}`} onClick={() => setMenuOpen(false)}>
      <span className="nav-icon">{item.icon}</span>
      <span>{item.label}</span>
      {item.badge}
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
        {viewer && <span className="chip readonly-chip">읽기 전용</span>}
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
        <div className="nav-utility">
          {UTILITY.map((item) => link(item, 'small'))}
          {onLogout && <button className="nav-link small nav-button" onClick={onLogout}>로그아웃</button>}
          {viewer && (
            <>
              <span className="nav-readonly small">읽기 전용 · 손님</span>
              <button className="nav-link small nav-button" onClick={viewer.onLogin}>관리자 로그인</button>
              <button className="nav-link small nav-button" onClick={viewer.onLeave}>나가기</button>
            </>
          )}
        </div>
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
