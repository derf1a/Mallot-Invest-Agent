/** 관리자 로그인 — 공유 링크 없이 들어온 사람에게 보인다. 손님은 관리자가 준 공유 링크로 들어온다. */
import { useState } from 'react'
import BrandMark from '../../app/BrandMark'
import { authApi, type Me } from './api'

export default function LoginPage({ me, onDone }: { me: Me; onDone: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await authApi.login(password)
      onDone()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <main className="login-page">
      <form className="card login-card" onSubmit={submit}>
        <div className="login-brand"><BrandMark size={28} /><b>Mallot Invest</b></div>
        {me.has_password ? (
          <>
            <label className="field">
              <span>관리자 비밀번호</span>
              <input type="password" autoComplete="current-password" autoFocus value={password}
                onChange={(e) => setPassword(e.target.value)} />
            </label>
            <button className="primary" disabled={busy || !password}>{busy ? '확인 중…' : '로그인'}</button>
            {error && <p className="error small">{error}</p>}
          </>
        ) : (
          <p className="muted">관리자 비밀번호가 정해지지 않아 로그인할 수 없어요.</p>
        )}
        <p className="muted small">운용 상황을 보러 오셨다면 관리자에게 받은 공유 링크로 들어와 주세요.</p>
      </form>
    </main>
  )
}
