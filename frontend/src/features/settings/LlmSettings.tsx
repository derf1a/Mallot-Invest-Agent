import { useEffect, useState } from 'react'
import { systemApi, type LlmProviderKey, type LlmStatus, type LlmTestResult, type LocalModel, type LocalWorkerState, type Thinking } from './api'

const gb = (v: number | null | undefined) => (v === null || v === undefined ? '–' : `${(v / 1024 ** 3).toFixed(1)} GB`)
const n = (v: number) => v.toLocaleString('ko-KR')

const WORKER_TEXT: Record<LocalWorkerState, string> = {
  stopped: '내려 있음',
  starting: '띄우는 중',
  loading: '올리는 중',
  ready: '올라감',
  error: '오류',
}

const THINKING: { key: Thinking; label: string }[] = [
  { key: 'off', label: '끔' },
  { key: 'low', label: '짧게' },
  { key: 'medium', label: '보통' },
  { key: 'xhigh', label: '깊게' },
]

/** 상태를 불러오고, 내려받는 중·올리는 중이면 2초마다 다시 본다. */
function useLlmStatus(refresh: number) {
  const [st, setSt] = useState<LlmStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const tick = async () => {
      try {
        const s = await systemApi.llmStatus()
        if (!alive) return
        setSt(s)
        setError(null)
        if (s.models.some((m) => m.downloading) || s.worker.state === 'starting' || s.worker.state === 'loading') {
          timer = window.setTimeout(tick, 2000)
        }
      } catch (e) {
        if (alive) setError((e as Error).message)
      }
    }
    tick()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [refresh])
  return { st, error }
}

/** 설정 > LLM: 기본 LLM(Claude / 로컬) 고르기, 로컬 모델 내려받기·올리기, GPU 메모리와 KV 캐시 */
export default function LlmSettings() {
  const [refresh, setRefresh] = useState(0)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { st, error } = useLlmStatus(refresh)

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true)
    setMessage(null)
    try {
      await fn()
      if (ok) setMessage(ok)
    } catch (e) {
      setMessage((e as Error).message)
    }
    setBusy(false)
    setRefresh((v) => v + 1)
  }

  if (error) return <p className="error">{error}</p>
  if (!st) return null
  const selected = st.models.find((m) => m.key === st.options.model) ?? st.models[0]

  return (
    <div className="resources llm-settings">
      <ProviderCard st={st} selected={selected} busy={busy} onPick={(p) => act(() => systemApi.setLlmProvider(p), p === 'local' ? '로컬 LLM 을 올리기 시작했어요' : 'Claude 로 바꿨어요')} />
      {message && <p className="muted small">{message}</p>}

      <article className="card">
        <h2>로컬 모델</h2>
        <p className="muted small">
          mlx-lm 으로 이 Mac 의 GPU 에서 돌려요. 모델 파일은 <code>data/models</code> 에 받고(커밋되지 않음), 받는 중에 꺼도 다시 누르면 이어서 받아요.
        </p>
        {st.models.map((m) => (
          <ModelRow
            key={m.key}
            m={m}
            chosen={m.key === st.options.model}
            busy={busy}
            onChoose={() => act(() => systemApi.setLocalOptions({ model: m.key }))}
            onDownload={() => act(() => systemApi.downloadLocalModel(m.key))}
            onCancel={() => act(() => systemApi.cancelLocalDownload(m.key))}
            onDelete={() => confirm(`${m.title} 파일(${gb(m.bytes)})을 지울까요?`) && act(() => systemApi.deleteLocalModel(m.key), '지웠어요')}
          />
        ))}
        <div className="llm-thinking">
          <span>생각 모드</span>
          <div className="segmented small" role="group" aria-label="생각 모드">
            {THINKING.map((t) => (
              <button key={t.key} className={st.options.thinking === t.key ? 'on' : ''} disabled={busy} onClick={() => act(() => systemApi.setLocalOptions({ thinking: t.key }))}>
                {t.label}
              </button>
            ))}
          </div>
          <span className="muted small">켜면 답하기 전에 먼저 생각해서 더 나을 수 있지만, 생각한 글자만큼 훨씬 느려져요.</span>
        </div>
      </article>

      <MemoryCard st={st} busy={busy} act={act} />
    </div>
  )
}

function ProviderCard({ st, selected, busy, onPick }: { st: LlmStatus; selected: LocalModel; busy: boolean; onPick: (p: LlmProviderKey) => void }) {
  const choices: { key: LlmProviderKey; title: string; lines: string[]; disabled?: string }[] = [
    { key: 'claude', title: 'Claude (Anthropic)', lines: ['Claude Code 로그인 · Agent SDK', '가장 정확하고 빠름 · 인터넷 필요'] },
    {
      key: 'local',
      title: `로컬 · ${selected.title}`,
      lines: ['이 Mac 의 GPU 에서 실행 · 요금 없음', '데이터가 Mac 밖으로 나가지 않음'],
      disabled: selected.downloaded ? undefined : '먼저 아래에서 모델을 내려받으세요',
    },
  ]
  return (
    <article className="card">
      <h2>기본 LLM</h2>
      <div className="llm-choices" role="radiogroup" aria-label="기본 LLM">
        {choices.map((c) => (
          <button
            key={c.key}
            role="radio"
            aria-checked={st.provider === c.key}
            className={`llm-choice${st.provider === c.key ? ' on' : ''}`}
            disabled={busy || !!c.disabled || st.provider === c.key}
            title={c.disabled}
            onClick={() => onPick(c.key)}
          >
            <b>{c.title}</b>
            {c.lines.map((l) => (
              <span key={l} className="muted small">
                {l}
              </span>
            ))}
            {c.disabled && <span className="small warn-text">{c.disabled}</span>}
          </button>
        ))}
      </div>
      <p className="muted small">
        기능마다 모델을 <b>기본 모델</b>로 두면 여기서 고른 LLM 을 써요. 실험실 &gt; 투자 에이전트·소비 습관·AI 리포트에서 모델을 직접 고른 곳은 그 선택을 따르고(로컬 LLM 도 고를 수 있어요),
        캡쳐 판독은 이미지를 읽어야 해서 언제나 Claude 를 써요.
      </p>
    </article>
  )
}

function ModelRow({ m, chosen, busy, onChoose, onDownload, onCancel, onDelete }: {
  m: LocalModel
  chosen: boolean
  busy: boolean
  onChoose: () => void
  onDownload: () => void
  onCancel: () => void
  onDelete: () => void
}) {
  return (
    <div className={`llm-model${chosen ? ' on' : ''}`}>
      <label className="llm-model-pick">
        <input type="radio" name="local-model" checked={chosen} disabled={busy} onChange={onChoose} />
        <span>
          <b>{m.title}</b> <span className="muted small mono">{m.repo}</span>
          <span className="muted small llm-note">{m.note}</span>
        </span>
      </label>
      <div className="llm-model-state">
        {m.downloading ? (
          <>
            <div className="progress" role="progressbar" aria-valuenow={Math.round((m.progress ?? 0) * 100)} aria-valuemax={100}>
              <span style={{ width: `${(m.progress ?? 0) * 100}%` }} />
            </div>
            <span className="small">
              {gb(m.bytes)} / {gb(m.total_bytes)}
            </span>
            <button disabled={busy} onClick={onCancel}>
              멈추기
            </button>
          </>
        ) : m.downloaded ? (
          <>
            <span className="chip paid">받음 · {gb(m.bytes)}</span>
            <button className="danger-text" disabled={busy} onClick={onDelete}>
              지우기
            </button>
          </>
        ) : (
          <>
            <span className="muted small">{m.bytes > 0 ? `${gb(m.bytes)} 받다 멈춤` : `약 ${gb(m.total_bytes)}`}</span>
            <button className="primary" disabled={busy} onClick={onDownload}>
              {m.bytes > 0 ? '이어 받기' : '내려받기'}
            </button>
          </>
        )}
      </div>
      {m.error && <p className="error small">{m.error}</p>}
    </div>
  )
}

function MemoryCard({ st, busy, act }: { st: LlmStatus; busy: boolean; act: (fn: () => Promise<unknown>, ok?: string) => Promise<void> }) {
  const [test, setTest] = useState<LlmTestResult | null>(null)
  const { worker, memory } = st
  const plan = worker.plan
  const total = memory.memory_bytes ?? 0
  const limit = plan?.limit_bytes ?? memory.gpu_limit_bytes ?? 0
  const raised = memory.gpu_limit_source === 'iogpu.wired_limit_mb'
  const loadedTitle = st.models.find((m) => m.key === worker.model)?.title
  const parts = plan
    ? [
        { label: '모델 가중치', value: plan.weights_bytes, color: 'var(--c-saving)' },
        { label: '고정 상태', value: plan.fixed_state_bytes, color: 'var(--c-fixed)', hint: '선형 어텐션 층의 상태 — 문맥 길이와 무관' },
        { label: `KV 캐시 · ${n(plan.kv_tokens)}토큰`, value: plan.kv_budget_bytes, color: 'var(--c-variable)' },
        { label: '여유분', value: plan.margin_bytes, color: 'var(--warn)', hint: '입력 처리 중 임시 계산용' },
        { label: 'GPU 한도 밖 (macOS·앱)', value: Math.max(0, total - limit), color: 'var(--border)' },
      ]
    : []

  return (
    <article className="card">
      <div className="section-head">
        <h2>
          GPU 메모리 · KV 캐시
          <span className={`health-badge ${worker.state === 'ready' ? 'ok' : worker.state === 'error' ? 'fail' : 'warn'}`}>{WORKER_TEXT[worker.state]}</span>
        </h2>
        <span className="toolbar">
          {worker.state === 'stopped' || worker.state === 'error' ? (
            <button className="primary" disabled={busy} onClick={() => act(() => systemApi.loadLocal())}>
              올리기
            </button>
          ) : (
            <button disabled={busy} onClick={() => act(() => systemApi.unloadLocal(), '내렸어요 — 메모리를 돌려줬어요')}>
              내리기
            </button>
          )}
          <button
            disabled={busy || worker.state !== 'ready'}
            onClick={() =>
              act(async () => {
                setTest(null)
                setTest(await systemApi.testLocal())
              })
            }
          >
            시험
          </button>
        </span>
      </div>
      {worker.error && <p className="error small">{worker.error}</p>}
      {plan ? (
        <>
          <div className="stack-bar llm-mem" aria-label="통합 메모리 배분">
            {parts.map((p) => (
              <span key={p.label} title={`${p.label} ${gb(p.value)}`} style={{ flex: Math.max(p.value, 1), background: p.color }} />
            ))}
          </div>
          <ul className="legend">
            {parts.map((p) => (
              <li key={p.label} title={p.hint}>
                <i style={{ background: p.color }} />
                {p.label} {gb(p.value)}
              </li>
            ))}
          </ul>
          <dl className="kv llm-kv">
            <dt>올린 모델</dt>
            <dd>
              {loadedTitle} · {worker.load_seconds}초 걸림
            </dd>
            <dt>GPU 한도</dt>
            <dd>
              {gb(plan.limit_bytes)} {plan.limit_source === 'iogpu.wired_limit_mb' ? '(iogpu.wired_limit_mb)' : '(macOS 권장값)'}
            </dd>
            <dt>KV 캐시</dt>
            <dd>
              {n(plan.kv_tokens)}토큰 · 토큰당 {Math.round(plan.kv_bytes_per_token / 1024)} KB · 모델 최대 {n(plan.max_context)}토큰
            </dd>
            <dt>지금 사용</dt>
            <dd>
              {gb(worker.active_bytes)} · 최고 {gb(worker.peak_bytes)}
              {worker.busy ? ` · 생성 중${worker.waiting ? `, 대기 ${worker.waiting}` : ''}` : ''} · 처리 {n(worker.served ?? 0)}건
            </dd>
          </dl>
        </>
      ) : (
        <p className="muted small">
          모델을 올리면 GPU 한도 {gb(limit)} 에서 가중치·고정 상태·여유분({gb(memory.margin_bytes)})을 빼고 남는 만큼을 KV 캐시로 잡아요. 입력이 그보다 길면 받지 않아서
          메모리 부족으로 Mac 이 멈추지 않아요.
        </p>
      )}
      {test && (
        <div className="llm-test small">
          <b>시험 답:</b> {test.answer || <span className="muted">(빈 답)</span>}
          <span className="muted">
            {' '}
            · 입력 {test.usage.prompt_tokens}토큰 {test.timing.prompt_tps ?? '–'} tok/s · 출력 {test.usage.output_tokens}토큰 {test.timing.generation_tps ?? '–'} tok/s · 전체{' '}
            {(test.total_ms / 1000).toFixed(1)}초
          </span>
        </div>
      )}
      <h3 className="small">GPU 메모리 한도</h3>
      <p className="muted small">
        Mac {gb(total)} 중 GPU 가 쓸 수 있는 한도는 지금 <b>{gb(memory.gpu_limit_bytes)}</b>
        {raised ? ` (iogpu.wired_limit_mb = ${n(memory.wired_limit_mb ?? 0)} MB 로 올려 둠)` : ' (macOS 기본값)'}이에요. 75%({gb(memory.target_bytes)})로 고정하려면 터미널에서{' '}
        <code>make gpu-mem</code> (관리자 암호 필요, 재부팅하면 풀림) 또는 <code>make gpu-mem ARGS=--persist</code> (부팅할 때마다 적용)을 실행한 뒤 모델을 내렸다 다시
        올리세요. 되돌리기는 <code>make gpu-mem ARGS=--reset</code>.
      </p>
    </article>
  )
}
