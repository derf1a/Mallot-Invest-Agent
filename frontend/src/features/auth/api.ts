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
}

export interface ShareLink { enabled: boolean; key: string; path: string; rotated_at: number | null }

export const authApi = {
  me: () => request<Me>('GET', '/api/auth/me'),
  login: (password: string) => request<{ role: Role }>('POST', '/api/auth/login', { password }),
  logout: () => request<{ role: Role }>('POST', '/api/auth/logout'),
  enter: (key: string) => request<{ role: Role }>('POST', '/api/auth/share/enter', { key }),
  share: () => request<ShareLink>('GET', '/api/auth/share'),
  setShare: (body: { enabled?: boolean; rotate?: boolean }) => request<ShareLink>('PUT', '/api/auth/share', body),
}
