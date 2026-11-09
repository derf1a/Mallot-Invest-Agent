/** 투자 화면의 작은 기억 — 최근 본 종목·마지막 탭 (브라우저 저장, 막혀 있어도 동작) */
// 브라우저 저장은 편의 기능 — 막혀 있어도 동작에는 지장 없다
export function load<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : (JSON.parse(v) as T)
  } catch {
    return fallback
  }
}
export function save(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* 저장 못 해도 괜찮다 */
  }
}

export interface Recent {
  symbol: string
  name: string
}
export const RECENT = 'invest.recent'
export function pushRecent(r: Recent) {
  save(RECENT, [r, ...load<Recent[]>(RECENT, []).filter((x) => x.symbol !== r.symbol)].slice(0, 12))
}
