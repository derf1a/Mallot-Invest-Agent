/** 접근 권한 API — 내 역할 · 관리자 로그인 · 손님 입장 · 공유 링크 관리 (백엔드 app/auth) */
import { request } from '../../shared/http'

export type Role = 'admin' | 'viewer' | null

export interface Me {
  /** 권한 검사가 켜져 있나 (관리자 비밀번호가 있거나 공개 모드) */
  auth: boolean
  public: boolean
  has_password: boolean
  role: Role
  share_enabled: boolean
  /** 홈 모드(MIA_HOME_AUTH_URL) — 로그인 · 손님 공유는 Mallot 홈이 맡는다. 없으면 앱 자체 로그인 */
  home?: boolean
  login_url?: string
  logout_url?: string
}

export interface ShareLink { enabled: boolean; key: string; path: string; rotated_at: number | null }

/** 접속 기록 (관리자만) — IP 하루 묶음 · 로그인 사건 · 한국 밖 경보 */
export interface AccessIp {
  day: string; ip: string; country: string | null; roles: string; requests: number; denied: number
  first_at: string; last_at: string; last_path: string; user_agent: string; foreign: boolean
}
export interface AccessEvent {
  id: number; day: string; at: string; ip: string; country: string | null; kind: string; kind_ko: string; ok: number
  note: string; user_agent: string; foreign: boolean
}
export interface AccessAlert { id: number; day: string; ip: string; country: string | null; reason: string; created_at: string; acked_at: string | null }
export interface AccessView {
  days: number; home_country: string; ips: AccessIp[]; events: AccessEvent[]; alerts: AccessAlert[]; open_alerts: number; note: string
}

/** 홈 모드 — Mallot 홈 로그인 화면으로 (돌아올 곳은 지금 주소) */
export function goHomeLogin(me: Me) {
  window.location.href = `${me.login_url ?? '/login'}?next=${encodeURIComponent(window.location.pathname + window.location.search)}`
}

/** 홈 모드 로그아웃 — 홈의 로그아웃을 부르고 홈으로 */
export async function homeLogout(me: Me) {
  try {
    await fetch(me.logout_url ?? '/logout', { method: 'POST', credentials: 'same-origin' })
  } finally {
    window.location.href = '/'
  }
}

export const authApi = {
  me: () => request<Me>('GET', '/api/auth/me'),
  login: (password: string) => request<{ role: Role }>('POST', '/api/auth/login', { password }),
  logout: () => request<{ role: Role }>('POST', '/api/auth/logout'),
  enter: (key: string) => request<{ role: Role }>('POST', '/api/auth/share/enter', { key }),
  share: () => request<ShareLink>('GET', '/api/auth/share'),
  setShare: (body: { enabled?: boolean; rotate?: boolean }) => request<ShareLink>('PUT', '/api/auth/share', body),
  access: (days = 14) => request<AccessView>('GET', `/api/auth/access?days=${days}`),
  alerts: () => request<AccessAlert[]>('GET', '/api/auth/access/alerts'),
  ack: (id?: number) => request<{ acked: number }>('POST', '/api/auth/access/ack', { id: id ?? null }),
}
