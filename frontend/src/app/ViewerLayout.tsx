/** 손님(공유 링크) 화면 뼈대 — 메뉴 없이 운용 현황 하나. 관리자는 여기서 로그인 화면으로 갈 수 있다. */
import type { ReactNode } from 'react'
import BrandMark from './BrandMark'

export default function ViewerLayout({ children, onLeave, onLogin }: { children: ReactNode; onLeave: () => void; onLogin: () => void }) {
  return (
    <div className="viewer-app">
      <header className="viewer-bar">
        <BrandMark size={22} />
        <b>Mallot Invest</b>
        <span className="chip">읽기 전용</span>
        <span className="viewer-actions">
          <button className="link small" onClick={onLogin}>관리자 로그인</button>
          <button className="link small" onClick={onLeave}>나가기</button>
        </span>
      </header>
      <main className="viewer-content">{children}</main>
    </div>
  )
}
