import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useLoad } from '../../shared/useLoad'
import { investApi, type InvestConfig, type KeyStatus, type Limits } from './api'
import { digits, grouped, won } from './format'

/** 설정 > 투자: 토스증권 API 키(.env), 연결 테스트·계좌 선택, 주문 조건(PIN), 전체 중지, 주문 한도. 주문은 언제나 실계좌. */
export default function InvestSettings() {
  const { data: cfg, error, reload } = useLoad(() => investApi.config(), [])
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setMessage(null)
    try {
      await fn()
      setMessage({ ok: true, text: ok })
      reload()
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) })
    }
  }

  if (error) return <p className="error">{error}</p>
  if (!cfg) return <p className="muted">불러오는 중…</p>
  return (
    <div className="invest-settings">
      {message && <p className={message.ok ? 'ok-text small' : 'error small'} role="status">{message.text}</p>}
      {cfg.keys && <KeysCard cfg={cfg} keys={cfg.keys} run={run} />}
      <TradingCard cfg={cfg} run={run} />
      <LimitsCard key={JSON.stringify(cfg.limits)} cfg={cfg} run={run} />
    </div>
  )
}

type Run = (fn: () => Promise<unknown>, ok: string) => Promise<void>

/** 키 칸 — 관리자만 (손님에게는 서버가 keys 를 보내지 않는다) */
function KeysCard({ cfg, keys, run }: { cfg: InvestConfig; keys: KeyStatus[]; run: Run }) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [tested, setTested] = useState<Awaited<ReturnType<typeof investApi.testConnection>> | null>(null)
  const [testing, setTesting] = useState(false)
  const [testError, setTestError] = useState<string | null>(null)
  const filled = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim()))

  const test = async () => {
    setTesting(true)
    setTestError(null)
    setTested(null)
    try {
      setTested(await investApi.testConnection())
    } catch (e) {
      setTestError(e instanceof Error ? e.message : String(e))
    } finally {
      setTesting(false)
    }
  }

  const source = (k: KeyStatus) => (k.source === 'file' ? '.env' : k.source === 'env' ? '셸 환경 변수' : '')
  return (
    <article className="card">
      <div className="section-head">
        <h2>
          토스증권 Open API
          <span className={`health-badge ${cfg.toss_configured ? 'ok' : 'warn'}`}>{cfg.toss_configured ? '키 있음' : '키 없음'}</span>
        </h2>
      </div>
      <p className="muted small">
        키는 <code>{cfg.env_file}</code> 에 저장돼요(git 제외, 권한 600). 화면에는 끝자리만 보여요. 토스증권 WTS &gt; Open API에서 이
        Mac의 공인 IP를 허용 IP로 등록해야 해요. 계좌 순번(accountSeq)은 계좌번호가 아니라 토스가 계좌마다 붙인 번호(보통 1)예요 —
        비워 두면 첫 계좌를 쓰고, 연결 테스트에서 고를 수 있어요(계좌번호를 넣어도 맞는 순번으로 바꿔 써요).
      </p>
      <div className="key-form">
        {keys.map((k) => (
          <label key={k.key} className="field">
            <span>
              {k.label} <span className="muted small mono">{k.key}</span>
            </span>
            <div className="key-row">
              <input type={k.secret ? 'password' : 'text'} autoComplete="off" spellCheck={false}
                placeholder={k.set ? `${k.masked} (${source(k)}) — 바꾸려면 입력` : '입력'}
                value={values[k.key] ?? ''} onChange={(e) => setValues((v) => ({ ...v, [k.key]: e.target.value }))} />
              {k.set && k.source === 'file' && (
                <button onClick={() => run(() => investApi.saveKeys({ clear: [k.key] }), `${k.label}를 지웠어요`)}>지우기</button>
              )}
            </div>
          </label>
        ))}
      </div>
      <div className="form-actions">
        <button className="primary" disabled={!Object.keys(filled).length}
          onClick={() => run(async () => {
            await investApi.saveKeys(filled)
            setValues({})
          }, '저장했어요 — 바로 적용돼요')}>
          저장
        </button>
        <button disabled={!cfg.toss_configured || testing} onClick={test}>{testing ? '확인 중…' : '연결 테스트'}</button>
      </div>
      {testError && <p className="error small">{testError}</p>}
      {tested && (
        <div className="accounts">
          <p className="ok-text small">연결됐어요 — 계좌 {tested.accounts.length}개</p>
          {tested.account_error && <p className="error small">{tested.account_error}</p>}
          <ul className="plain-list">
            {tested.accounts.map((a) => {
              const using = String(a.account_seq) === tested.selected_seq
              return (
                <li key={a.account_seq} className="key-row">
                  <span>계좌 {a.account_no} · {a.account_type} · 순번 {a.account_seq}</span>
                  {using ? (
                    <span className="health-badge ok">사용 중</span>
                  ) : (
                    <button onClick={() => run(async () => {
                      await investApi.saveKeys({ TOSS_ACCOUNT_SEQ: String(a.account_seq) })
                      setTested(null)
                    }, '이 계좌를 쓰도록 저장했어요')}>
                      이 계좌 사용
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </article>
  )
}

function TradingCard({ cfg, run }: { cfg: InvestConfig; run: Run }) {
  const blocked = cfg.trading_blockers.length > 0
  return (
    <article className="card">
      <h2>
        주문
        <span className={`health-badge ${blocked ? 'warn' : 'ok'}`}>{blocked ? '주문 불가' : '실계좌 주문 가능'}</span>
      </h2>
      <p className="muted small">
        모의 투자는 없어요 — 주문은 언제나 토스증권 실계좌로 나가요. 주문·승인·에이전트 실행과 키·한도·명단 변경 때 주문 PIN 을 물어요
        (5번 틀리면 5분 잠금). 전체 주문 중지는 PIN 없이 켤 수 있어요.
      </p>
      {blocked && (
        <div className="small">
          <p className="muted">주문하려면:</p>
          <ul className="plain-list">
            {cfg.trading_blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
          {!cfg.pin_configured && (
            <p className="muted">
              <code>{cfg.env_file ?? '.env'}</code> 에 <code>{cfg.pin_env_name}=숫자4자리이상</code> 을 직접 추가하고 서버를 다시 시작하세요. 화면 · API 로는
              PIN 을 정하거나 바꿀 수 없어요 (이 Mac 에서만).
            </p>
          )}
        </div>
      )}
      <p className="muted small">
        AI 에이전트 주문은 기본으로 AI Trading 화면에서 내가 승인해야 나가요. 승인 생략과 에이전트 구성은 <Link to="/invest/team">실험실 &gt; 투자 에이전트</Link>에서 바꿔요.
      </p>
      <label className="check halt">
        <input type="checkbox" checked={cfg.halted}
          onChange={(e) => run(() => investApi.saveTrading({ halted: e.target.checked }), e.target.checked ? '모든 주문을 멈췄어요' : '주문을 다시 받아요')} />
        <b>전체 주문 중지</b> — 수동·에이전트 주문을 모두 막아요
      </label>
    </article>
  )
}

function LimitsCard({ cfg, run }: { cfg: InvestConfig; run: Run }) {
  const [limits, setLimits] = useState<Limits>(cfg.limits)
  const field = (key: keyof Limits, label: string, suffix: string) => (
    <label className="field">
      <span>{label}</span>
      <div className="key-row">
        <input inputMode="numeric" value={key === 'max_position_pct' ? limits[key] : grouped(String(limits[key]))}
          onChange={(e) => setLimits((l) => ({ ...l, [key]: Number(key === 'max_position_pct' ? e.target.value.replace(/[^\d.]/g, '') : digits(e.target.value)) }))} />
        <span className="muted small">{suffix}</span>
      </div>
    </label>
  )
  return (
    <article className="card">
      <h2>주문 한도</h2>
      <p className="muted small">모든 주문(수동·에이전트) 앞에서 코드가 검사해요. 하루 한도는 이 앱에서 낸 주문만 셉니다.</p>
      <div className="grid">
        {field('max_order_krw', '건당 최대 금액', `원 (${won(limits.max_order_krw)})`)}
        {field('daily_max_krw', '하루 최대 금액', `원 (${won(limits.daily_max_krw)})`)}
        {field('max_position_pct', '종목당 최대 비중', '% (매수 후 기준)')}
      </div>
      <div className="form-actions">
        <button className="primary" onClick={() => run(() => investApi.saveTrading({ limits }), '한도를 저장했어요')}>저장</button>
      </div>
    </article>
  )
}
