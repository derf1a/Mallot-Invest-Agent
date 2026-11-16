/**
 * 다른 Mallot 앱(가계부 · 투자 · 실험실) 주소 — 지금 주소에서 만든다. 도메인을 코드에 적지 않는다.
 * ledger.<도메인> ↔ invest.<도메인> ↔ lab.<도메인>, 이 컴퓨터에서는 백엔드 포트 8200 · 8100 · 8400, 개발 서버 3200 · 3100 · 3400.
 * 모르면 빈 목록 (링크를 숨긴다). 로그인은 앱마다 따로라, 처음 넘어가면 그쪽에서 한 번 로그인한다.
 */
export interface OtherApp {
  key: AppKey
  label: string
  url: string
}

type AppKey = 'ledger' | 'invest' | 'lab'
const APPS: { key: AppKey; label: string; serve: string; dev: string }[] = [
  { key: 'ledger', label: 'Mallot Ledger', serve: '8200', dev: '3200' },
  { key: 'invest', label: 'Mallot Invest', serve: '8100', dev: '3100' },
  { key: 'lab', label: 'Mallot Lab', serve: '8400', dev: '3400' },
]
const LOCAL = new Set(['localhost', '127.0.0.1'])

function current(here: Location): { key: AppKey; rest: string[]; port?: 'serve' | 'dev' } | null {
  const [first, ...rest] = here.hostname.split('.')
  const bySub = APPS.find((a) => a.key === first)
  if (bySub && rest.length) return { key: bySub.key, rest }
  if (LOCAL.has(here.hostname)) {
    const serve = APPS.find((a) => a.serve === here.port)
    if (serve) return { key: serve.key, rest: [], port: 'serve' }
    const dev = APPS.find((a) => a.dev === here.port)
    if (dev) return { key: dev.key, rest: [], port: 'dev' }
  }
  return null
}

export function otherApps(here: Location = window.location): OtherApp[] {
  const at = current(here)
  if (!at) return []
  return APPS.filter((a) => a.key !== at.key).map((a) => ({
    key: a.key,
    label: a.label,
    url: at.port
      ? `${here.protocol}//${here.hostname}:${a[at.port]}/`
      : `${here.protocol}//${[a.key, ...at.rest].join('.')}/`,
  }))
}

/** 맨 도메인의 Mallot 홈 (ledger.<도메인> → <도메인>). 하위 도메인이 아니면 null (링크를 숨긴다) */
export function portalUrl(here: Location = window.location): string | null {
  const at = current(here)
  return at && !at.port && at.rest.length >= 2 ? `${here.protocol}//${at.rest.join('.')}/` : null
}
