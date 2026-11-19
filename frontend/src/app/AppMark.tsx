import { useId } from 'react'
import { APP_LABEL, type AppKey } from './appKeys'
import BrandMark from './BrandMark'

/**
 * Mallot 앱 마크 — 홈(Mallot 'M') · 가계부(영수증) · 투자(오르는 캔들) · 실험실(플라스크).
 * 같은 둥근 사각형 + 흰 선, 앱마다 색만 다르다 (홈 대시보드의 앱 색과 같은 계열: 초록 · 파랑 · 보라).
 * 세 앱 저장소에 같은 파일이 있다 — 바꾸면 셋 다, 그리고 public/favicon.svg · mallot-home/portal/static 도 같이.
 */
const COLORS: Record<Exclude<AppKey, 'home'>, [string, string]> = {
  ledger: ['#2bb38a', '#126650'],
  invest: ['#3f8ad6', '#163a6b'],
  lab: ['#9a6ed3', '#432474'],
}

function Glyph({ app }: { app: Exclude<AppKey, 'home'> }) {
  const line = { fill: 'none', stroke: '#fff', strokeLinecap: 'round', strokeLinejoin: 'round' } as const
  if (app === 'ledger') {
    return (
      <>
        <path {...line} strokeWidth={2.2} d="M10.5 7h11v18l-1.84-1.3-1.83 1.3-1.83-1.3-1.83 1.3-1.84-1.3-1.83 1.3z" />
        <path {...line} strokeWidth={2.2} d="M13.6 12h4.8M13.6 15.8h4.8M13.6 19.6h2.8" />
      </>
    )
  }
  if (app === 'invest') {
    return (
      <>
        <path {...line} strokeWidth={1.8} strokeOpacity={0.8} d="M10 13.5v10M16 9.5v11M22 6v10.5" />
        <rect x="8.3" y="15.5" width="3.4" height="6" rx="1" fill="#fff" />
        <rect x="14.3" y="12" width="3.4" height="6" rx="1" fill="#fff" />
        <rect x="20.3" y="8" width="3.4" height="5.5" rx="1" fill="#fff" />
      </>
    )
  }
  return (
    <>
      <path d="M11.3 19.4h9.4l2.1 3.6a.9.9 0 0 1-.78 1.35H9.98a.9.9 0 0 1-.78-1.35z" fill="#fff" fillOpacity={0.45} />
      <path {...line} strokeWidth={2.1} d="M13.5 6.5h5M14.5 6.5v6.6l-5.6 9.6a1.7 1.7 0 0 0 1.47 2.55h11.26a1.7 1.7 0 0 0 1.47-2.55l-5.6-9.6V6.5" />
      <circle cx="15" cy="16.4" r="0.95" fill="#fff" />
      <circle cx="17.5" cy="14.2" r="0.7" fill="#fff" />
    </>
  )
}

export default function AppMark({ app, size = 30, title }: { app: AppKey; size?: number; title?: string }) {
  const id = useId().replace(/:/g, '')
  if (app === 'home') return <BrandMark size={size} />
  const [from, to] = COLORS[app]
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label={title ?? `Mallot ${APP_LABEL[app]}`} className="brand-mark">
      <defs>
        <linearGradient id={`${id}-bg`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={from} />
          <stop offset="1" stopColor={to} />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${id}-bg)`} />
      <rect x="0.5" y="0.5" width="31" height="31" rx="8.5" fill="none" stroke="#fff" strokeOpacity="0.12" />
      <Glyph app={app} />
    </svg>
  )
}
