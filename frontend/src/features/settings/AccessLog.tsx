/**
 * Settings > 접속 기록 (관리자만) — 한국 밖 경보 · IP 별 하루 묶음 · 로그인 사건.
 * IP · 나라는 Cloudflare 가 붙인 값(CF-Connecting-IP · CF-IPCountry). local = 이 서버 안에서 온 요청. 90일 지나면 지운다.
 */
import { useState } from 'react'
import { useLoad } from '../../shared/useLoad'
import { authApi } from '../auth/api'

const DAYS = [1, 7, 14, 30, 90]
const flag = (c: string | null) => (c && /^[A-Z]{2}$/.test(c) && c !== 'XX' && c !== 'T1'
  ? String.fromCodePoint(...[...c].map((ch) => 0x1f1a5 + ch.charCodeAt(0))) : '')
const where = (c: string | null) => (c === null ? '이 서버' : c === 'XX' ? '알 수 없음' : c === 'T1' ? 'Tor' : `${flag(c)} ${c}`)
const ua = (s: string) => {
  const os = /iPhone|iPad/.test(s) ? 'iOS' : /Android/.test(s) ? 'Android' : /Mac OS/.test(s) ? 'Mac' : /Windows/.test(s) ? 'Windows' : /Linux/.test(s) ? 'Linux' : ''
  const br = /Edg\//.test(s) ? 'Edge' : /CriOS|Chrome\//.test(s) ? 'Chrome' : /Safari\//.test(s) ? 'Safari' : /Firefox/.test(s) ? 'Firefox' : /curl/.test(s) ? 'curl' : ''
  return [os, br].filter(Boolean).join(' · ') || (s ? s.slice(0, 40) : '—')
}

export default function AccessLog() {
  const [days, setDays] = useState(14)
  const { data, error, reload } = useLoad(() => authApi.access(days), [days])
  const ack = async (id?: number) => { await authApi.ack(id); reload(); window.dispatchEvent(new Event('access-alerts-changed')) }
  if (error) return <p className="error">{error}</p>
  if (!data) return <p className="muted">불러오는 중…</p>
  const open = data.alerts.filter((a) => !a.acked_at)
  return (
    <>
      <article className={`card${open.length ? ' access-alert-card' : ''}`}>
        <h2>한국 밖 접속 경보 {open.length > 0 && <span className="chip warn-chip">{open.length}</span>}</h2>
        <p className="muted small">나라가 {data.home_country} 가 아닌 IP(알 수 없음 · Tor 포함)가 오면 하루에 IP 마다 한 번 남기고 화면 위에 띄워요.</p>
        {data.alerts.length === 0 ? <p className="muted small">아직 없어요.</p> : (
          <table className="data-table">
            <thead><tr><th>때</th><th>IP</th><th>나라</th><th>무엇</th><th /></tr></thead>
            <tbody>
              {data.alerts.map((a) => (
                <tr key={a.id} className={a.acked_at ? 'muted' : ''}>
                  <td className="mono small">{a.created_at.slice(5, 16)}</td>
                  <td className="mono">{a.ip}</td>
                  <td>{where(a.country)}</td>
                  <td className="small">{a.reason}</td>
                  <td>{a.acked_at ? <span className="small muted">확인함</span> : <button className="small" onClick={() => ack(a.id)}>확인</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {open.length > 1 && <div className="form-actions"><button onClick={() => ack()}>모두 확인</button></div>}
      </article>

      <article className="card">
        <h2>접속 IP</h2>
        <div className="form-actions access-days">
          {DAYS.map((d) => <button key={d} className={d === days ? 'primary small' : 'small'} onClick={() => setDays(d)}>{d}일</button>)}
          <button className="small" onClick={reload}>새로고침</button>
        </div>
        <p className="muted small">{data.note}. 하루에 IP 한 줄 — 역할은 그날 쓴 것(admin 관리자 · viewer 손님 · none 로그인 전).</p>
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>날</th><th>IP</th><th>나라</th><th>역할</th><th>요청</th><th>거절</th><th>처음 · 마지막</th><th>마지막 요청</th><th>기기</th></tr></thead>
            <tbody>
              {data.ips.map((r) => (
                <tr key={`${r.day}-${r.ip}`} className={r.foreign ? 'access-foreign' : ''}>
                  <td className="mono small">{r.day.slice(5)}</td>
                  <td className="mono">{r.ip}</td>
                  <td>{where(r.country)}</td>
                  <td className="small">{r.roles}</td>
                  <td className="num">{r.requests.toLocaleString('ko-KR')}</td>
                  <td className={`num${r.denied ? ' warn-text' : ''}`}>{r.denied}</td>
                  <td className="mono small">{r.first_at.slice(11, 16)} · {r.last_at.slice(11, 16)}</td>
                  <td className="mono small">{r.last_path}</td>
                  <td className="small" title={r.user_agent}>{ua(r.user_agent)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      <article className="card">
        <h2>로그인 · 손님 입장</h2>
        {data.events.length === 0 ? <p className="muted small">아직 없어요.</p> : (
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>때</th><th>무엇</th><th>결과</th><th>IP</th><th>나라</th><th>기기</th></tr></thead>
              <tbody>
                {data.events.map((e) => (
                  <tr key={e.id} className={e.foreign ? 'access-foreign' : ''}>
                    <td className="mono small">{e.at.slice(5, 16)}</td>
                    <td>{e.kind_ko}</td>
                    <td className={e.ok ? 'pos' : 'warn-text'}>{e.ok ? '성공' : `실패${e.note ? ` · ${e.note}` : ''}`}</td>
                    <td className="mono">{e.ip}</td>
                    <td>{where(e.country)}</td>
                    <td className="small" title={e.user_agent}>{ua(e.user_agent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>
    </>
  )
}
