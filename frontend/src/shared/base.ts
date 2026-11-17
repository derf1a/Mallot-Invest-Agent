/**
 * 경로 접두사 — 빌드할 때 MIA_BASE_PATH(예: /invest)가 있으면 '/invest/', 없으면 '/' (vite.config.ts 의 base).
 * 루트 기준 절대 경로(/api/… · /share?k=…)는 모두 withBase 를 거쳐 이 앱 아래로 보낸다.
 * 라우터 안의 경로(<Link to> · navigate)는 BrowserRouter basename 이 붙이니 그대로 둔다.
 */
export const BASE_URL: string = import.meta.env.BASE_URL || '/'

/** '/api/x' → '/invest/api/x' (접두사가 없으면 그대로). 상대 경로 · 전체 URL 은 건드리지 않는다 */
export function withBase(path: string): string {
  if (!path.startsWith('/') || path.startsWith('//')) return path
  return BASE_URL.replace(/\/$/, '') + path
}
