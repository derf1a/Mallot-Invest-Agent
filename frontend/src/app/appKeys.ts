/** Mallot 앱 키와 화면 이름 — AppMark · AppSwitcher 가 같이 쓴다 (세 앱 저장소에 같은 파일) */
export type AppKey = 'home' | 'ledger' | 'invest' | 'lab'

export const APP_LABEL: Record<AppKey, string> = { home: 'Home', ledger: 'Ledger', invest: 'Invest', lab: 'Lab' }
