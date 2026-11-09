/**
 * Settings > LLM > 경로 — 기본 경로와 기능별 경로, vLLM 연결. 에이전트별 경로는 Agents 화면에서 에이전트마다 고른다.
 * 경로 글자: claude · claude-<모델> · mlx · vllm · vllm:<모델>. vLLM 주소 · 키는 .env 로만 (MIA_VLLM_URL · MIA_VLLM_API_KEY).
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useLoad } from '../../shared/useLoad'
import { systemApi } from './api'

const EXAMPLES = ['claude', 'claude-sonnet-5-5', 'mlx', 'vllm', 'vllm:Qwen/Qwen3-32B']

export default function RoutesSettings() {
  const { data: view, error, reload } = useLoad(() => systemApi.routes(), [])
  const { data: vllm, reload: reloadVllm } = useLoad(() => systemApi.vllm(), [])
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<string | null>(null)
  const [test, setTest] = useState<string | null>(null)
  if (error) return <p className="error">{error}</p>
  if (!view) return <p className="muted">불러오는 중…</p>
  const val = (k: string, saved: string) => draft[k] ?? saved
  const save = async () => {
    const features: Record<string, string> = {}
    for (const f of view.feature_list) if (draft[`f:${f.key}`] !== undefined) features[f.key] = draft[`f:${f.key}`]
    try {
      await systemApi.setRoutes({ ...(draft.default !== undefined ? { default: draft.default } : {}), features })
      setDraft({})
      setMsg('저장했어요')
      reload()
    } catch (e) {
      setMsg((e as Error).message)
    }
  }
  return (
    <div className="card stack">
      <h3>LLM 경로</h3>
      <p className="muted small">
        호출 하나가 어디로 갈지는 <b>에이전트 경로</b>(<Link to="/observe/agents">Agents</Link>) → 토론 설정의 모델 → <b>기능 경로</b> → <b>기본 경로</b> 순으로 정해요.
        예: <code>{EXAMPLES.join(' · ')}</code>
      </p>
      <label className="field">
        <span>기본 경로 <span className="muted small">지금: {view.effective_default.label}</span></span>
        <input value={val('default', view.default)} placeholder="비우면 위의 기본 LLM (Claude / 로컬)" list="route-examples"
          onChange={(e) => setDraft({ ...draft, default: e.target.value })} />
      </label>
      <table className="data-table">
        <thead><tr><th>기능</th><th>경로</th><th>지금 가는 곳</th></tr></thead>
        <tbody>
          {view.feature_list.map((f) => (
            <tr key={f.key}>
              <td>{f.title}<div className="muted small"><code>{f.key}</code></div></td>
              <td><input value={val(`f:${f.key}`, view.features[f.key] ?? '')} list="route-examples" placeholder="기본 따름"
                onChange={(e) => setDraft({ ...draft, [`f:${f.key}`]: e.target.value })} /></td>
              <td className="small">{f.route.label}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <datalist id="route-examples">{EXAMPLES.map((x) => <option key={x} value={x} />)}</datalist>
      <div className="row-actions">
        <button className="primary" disabled={!Object.keys(draft).length} onClick={save}>경로 저장</button>
        {msg && <span className="small">{msg}</span>}
      </div>
      {Object.keys(view.agents).length > 0 && (
        <p className="small">에이전트 경로: {Object.entries(view.agents).map(([k, v]) => `${k} → ${v}`).join(' · ')}</p>
      )}

      <h3>vLLM (OpenAI 호환 서버)</h3>
      {vllm && !vllm.configured && (
        <p className="muted small">
          주소가 없어요. <code>.env</code> 에 <code>MIA_VLLM_URL=http://127.0.0.1:8001/v1</code> 을 넣고 다시 시작하세요.
          도구 호출을 쓰려면 vLLM 을 <code>--enable-auto-tool-choice --tool-call-parser &lt;모델에 맞는 것&gt;</code> 으로 띄웁니다.
        </p>
      )}
      {vllm?.configured && (
        <div className="stack">
          <p className="small">
            {vllm.url} {vllm.has_key && '· 키 있음'} · {vllm.reachable ? <span className="pos">연결됨</span> : <span className="error">연결 안 됨 {vllm.error}</span>}
          </p>
          <label className="field">
            <span>기본 모델 <span className="muted small">경로에 모델 이름이 없을 때</span></span>
            <select value={vllm.model} onChange={(e) => systemApi.setVllmModel(e.target.value).then(reloadVllm)}>
              <option value="">서버의 첫 모델</option>
              {vllm.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <div className="row-actions">
            <button onClick={() => { setTest('묻는 중…'); systemApi.testVllm().then((r) => setTest(`${r.answer} (${r.model}, ${r.total_ms}ms)`)).catch((e: Error) => setTest(e.message)) }}>
              시험
            </button>
            {test && <span className="small">{test}</span>}
          </div>
        </div>
      )}
    </div>
  )
}
