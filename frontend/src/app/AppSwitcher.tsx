import { otherApps, portalUrl } from '../shared/otherApp'
import AppMark from './AppMark'
import { APP_LABEL, type AppKey } from './appKeys'

/**
 * 사이드바 아래 Mallot 앱 바꾸기 — 홈 · 가계부 · 투자 · 실험실을 아이콘 타일 한 줄로. 지금 앱은 눌린 모양(링크 아님).
 * 다른 앱 주소를 모르면(이 컴퓨터의 낯선 포트 등) 통째로 숨긴다. 세 앱 저장소에 같은 파일이 있다.
 */
const ORDER: AppKey[] = ['home', 'ledger', 'invest', 'lab']

export default function AppSwitcher({ self, note }: { self: Exclude<AppKey, 'home'>; note?: string }) {
  const others = new Map<AppKey, string>(otherApps().map((o) => [o.key, o.url]))
  const portal = portalUrl()
  if (portal) others.set('home', portal)
  if (others.size === 0) return null
  return (
    <div className="app-switcher" role="navigation" aria-label="Mallot 앱 바꾸기">
      {ORDER.map((key) => {
        const label = APP_LABEL[key]
        if (key === self) {
          return (
            <span key={key} className="app-tile current" aria-current="page" title={`Mallot ${label} — 지금 보는 앱`}>
              <AppMark app={key} size={24} />
              <span>{label}</span>
            </span>
          )
        }
        const url = others.get(key)
        if (!url) return null
        const tip = key === 'home' ? 'Mallot 홈 — 통합 대시보드' : `Mallot ${label}${note ? ` — ${note}` : ''}`
        return (
          <a key={key} className="app-tile" href={url} title={tip}>
            <AppMark app={key} size={24} />
            <span>{label}</span>
          </a>
        )
      })}
    </div>
  )
}
