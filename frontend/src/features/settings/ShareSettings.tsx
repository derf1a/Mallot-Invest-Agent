/**
 * Settings > 공유 — 손님(읽기 전용) 공유 링크 켜기 · 끄기 · 새 키, 휴대폰으로 찍어 여는 QR.
 * 손님은 운용 상황 · 보고서만 본다 (/share). 관리자는 같은 주소에서 비밀번호로 로그인하면 휴대폰에서도 모든 화면을 쓴다.
 */
import QRCode from 'qrcode'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useLoad } from '../../shared/useLoad'
import { authApi } from '../auth/api'

export default function ShareSettings() {
  const { data: me } = useLoad(() => authApi.me(), [])
  const { data: link, error, reload } = useLoad(() => authApi.share(), [])
  const [qr, setQr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const url = link ? `${window.location.origin}${link.path}` : ''
  const adminUrl = `${window.location.origin}/`

  useEffect(() => {
    if (!link?.enabled) return
    QRCode.toDataURL(url, { margin: 1, width: 220 }).then(setQr).catch(() => setQr(null))
  }, [link?.enabled, url])

  const act = async (body: { enabled?: boolean; rotate?: boolean }, done: string) => {
    try {
      await authApi.setShare(body)
      setMsg(done)
      reload()
    } catch (e) {
      setMsg((e as Error).message)
    }
  }
  const copy = (text: string) => navigator.clipboard?.writeText(text).then(() => setMsg('복사했어요'))

  return (
    <div className="card stack">
      <h3>접근 권한</h3>
      {me && (
        <p className="small">
          {me.auth ? <span className="chip paid">로그인 켜짐</span> : <span className="chip missed">로그인 꺼짐</span>}{' '}
          {me.auth
            ? '관리자 화면은 비밀번호로 로그인해야 열려요. 휴대폰에서도 같은 주소로 들어와 로그인하면 모든 화면을 쓸 수 있어요.'
            : '이 컴퓨터에서만 쓰는 모드예요. 외부(터널)에서 오는 요청은 막혀요 — 공개하려면 관리자 비밀번호를 먼저 정하세요 (docs/share.md).'}
        </p>
      )}

      <h3>손님 공유 링크 <span className="muted small">운용 상황 · 보고서만, 읽기 전용</span></h3>
      {error && <p className="error">{error}</p>}
      {link && (
        <>
          <div className="row-actions">
            <button className={link.enabled ? '' : 'primary'} onClick={() => act({ enabled: !link.enabled }, link.enabled ? '공유를 껐어요 — 손님이 바로 막혀요' : '공유를 켰어요')}>
              {link.enabled ? '공유 끄기' : '공유 켜기'}
            </button>
            <button onClick={() => window.confirm('새 키를 만들면 지금 링크와 이미 들어온 손님이 모두 끊겨요. 만들까요?') && act({ rotate: true }, '새 키를 만들었어요')}>
              새 키 만들기
            </button>
            <Link className="small" to="/share">손님 화면 미리 보기 →</Link>
          </div>
          {link.enabled ? (
            <div className="share-link">
              {qr && <img src={qr} alt="공유 링크 QR 코드 — 휴대폰 카메라로 찍으면 열려요" width={180} height={180} />}
              <div className="stack">
                <code className="share-url">{url}</code>
                <div className="row-actions"><button className="small" onClick={() => copy(url)}>링크 복사</button></div>
                <p className="muted small">
                  이 주소를 아는 사람은 누구나 운용 상황 · 보고서(금액 포함)를 볼 수 있어요. 키는 링크에만 있고, 들어온 손님은 30일 동안 다시 묻지 않아요.
                  {window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
                    ? ' 지금은 이 컴퓨터 주소예요 — 휴대폰에서는 터널 주소(내 도메인)로 열어야 해요.' : ''}
                </p>
              </div>
            </div>
          ) : <p className="muted small">공유가 꺼져 있어요. 켜면 링크와 QR 코드가 보여요.</p>}
        </>
      )}

      <h3>내 휴대폰에서 관리자로</h3>
      <p className="small">
        휴대폰 브라우저에서 <code>{adminUrl}</code> 을 열고 관리자 비밀번호로 로그인하세요. 공유(손님)로 들어와 있었다면 로그인하면 관리자로 바뀌어요.
        홈 화면에 추가하면 앱처럼 열려요 (iPhone: 공유 → 홈 화면에 추가).
      </p>
      {msg && <p className="small">{msg}</p>}
    </div>
  )
}
