/**
 * Team > 성과 · 가중치 — 에이전트마다 지난 판단 성적(코스피 대비)과, 그 성적이 실제 의사결정에서 표의 무게로 얼마나 쓰이는지.
 * 상시 운용 입장판은 한 표 = 확신 ÷ conviction_unit × 성과 배수로 무게를 단다 (백엔드 debate/track.py · stream.py).
 * 정기 회의는 무게 없이 머릿수(과반 + 반대 극소수)로 정한다.
 */
import { useLoad } from '../../shared/useLoad'
import { AgentAvatar } from './agents/Face'
import { nameOf, type Person } from './agents/people'
import { investApi, type AgentInfluence, type AgentScore } from './api'
import { pct, ratio } from './format'

interface Row { key: string; p: Person; score?: AgentScore; inf?: AgentInfluence }

export default function AgentWeights() {
  const { data, error, reload } = useLoad(() => investApi.agentScores(), [])
  const { data: team } = useLoad(() => investApi.agents(), [])
  if (error) return <p className="error">{error}</p>
  if (!data) return <p className="muted">불러오는 중… (지난 판단을 시세로 채점해요)</p>

  const people = new Map<string, Person>()
  for (const a of [...(team?.roster ?? []), ...(team?.desk.people ?? [])]) people.set(a.key, a)
  const keys = [...new Set([...Object.keys(data.influence), ...Object.keys(data.scores)])]
  const rows: Row[] = keys
    .map((key) => ({ key, p: people.get(key) ?? { key, title: key }, score: data.scores[key], inf: data.influence[key] }))
    .sort((a, b) => (b.inf?.share ?? 0) - (a.inf?.share ?? 0) || (b.score?.skill ?? 1) - (a.score?.skill ?? 1))
  const r = data.rules
  const top = Math.max(...rows.map((x) => x.inf?.share ?? 0), 0.0001)

  return (
    <div className="weights-page">
      <article className="card weights-rules">
        <div className="section-head">
          <h2>어떻게 정해지나 <span className="chip">{r.mode === 'continuous' ? '지금: 상시 운용' : '지금: 정기 회의'}</span></h2>
          <button className="small" onClick={reload}>다시 채점</button>
        </div>
        <ul className="small">
          <li>
            <b>한 표의 무게</b> = 확신 ÷ {r.conviction_unit} × 성과 배수 — 확신 {r.conviction_unit} 이 한 표, 성과 배수는 {r.min_skill}~{r.max_skill} 사이.
          </li>
          <li>
            <b>성과 배수</b> = 적중률 × 2. 판단 뒤 {data.horizon} 거래일 수익률에서 같은 기간 {data.benchmark} 변화를 빼고 방향이 맞았는지 봐요.
            표본이 적을 때 흔들리지 않게 맞음 · 틀림을 {r.prior} 번씩 더해 두고 시작해요.
          </li>
          <li>
            <b>상시 운용</b>: 종목마다 한쪽 무게 합이 {r.propose_min} 이상이면 안건, {r.for_min} 이상이고 반대 무게가 {r.against_max} 이하면 주문
            (매도는 {r.for_min} 이상 + 반대보다 무거우면).
          </li>
          <li><b>정기 회의</b>: 무게 없이 머릿수 — 과반이 같은 방향이고 반대가 {Math.round(r.max_oppose_share * 100)}% 이하일 때만 주문.</li>
        </ul>
      </article>

      <article className="card">
        <h2>에이전트별 <span className="muted small">최근 입장 {r.recent}개 · 표의 비중 순</span></h2>
        {!rows.length && <p className="muted small">아직 기록된 매수 · 매도 입장이 없어요. 토론이 돌면 쌓여요.</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table className="data-table weights-table">
              <thead>
                <tr>
                  <th>에이전트</th>
                  <th title="모든 에이전트 무게 합 가운데 이 에이전트 몫">표의 비중</th>
                  <th title="평균 (확신 ÷ 0.5 × 성과 배수)">평균 한 표</th>
                  <th title={`${r.min_skill}~${r.max_skill}`}>성과 배수</th>
                  <th>적중률</th>
                  <th title={`판단 뒤 ${data.horizon} 거래일, ${data.benchmark} 대비 (방향 반영)`}>{data.benchmark} 대비</th>
                  <th>입장 (매수 · 매도)</th>
                  <th title="같은 날 같은 종목에 실제로 나간 에이전트 주문과">주문과 같은 편 · 반대</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ key, p, score, inf }) => {
                  const skill = score?.skill ?? 1
                  return (
                    <tr key={key}>
                      <td>
                        <span className="weights-who">
                          <AgentAvatar p={p} className="mini" />
                          <span><b>{nameOf(p)}</b>{p.name && <span className="muted small"> {p.title}</span>}</span>
                        </span>
                      </td>
                      <td>
                        <span className="weights-share">
                          <span className="weights-bar" style={{ width: `${((inf?.share ?? 0) / top) * 70}px` }} />
                          <span>{inf ? ratio(inf.share) : '–'}</span>
                        </span>
                      </td>
                      <td>{inf ? inf.avg_weight.toFixed(2) : '–'} <span className="muted small">{inf ? `확신 ${inf.avg_confidence.toFixed(2)}` : ''}</span></td>
                      <td className={skill > 1 ? 'up' : skill < 1 ? 'down' : ''}>×{skill.toFixed(2)}</td>
                      <td>{score ? `${ratio(score.hit, 0)} (${score.wins}/${score.judged})` : <span className="muted">채점 전</span>}</td>
                      <td className={score ? (score.excess > 0 ? 'up' : score.excess < 0 ? 'down' : '') : ''}>{score ? pct(score.excess) : '–'}</td>
                      <td>{inf ? `${inf.views} (${inf.buy} · ${inf.sell})` : '–'}</td>
                      <td>{inf ? `${inf.with_orders} · ${inf.against_orders}` : '–'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">
          성과 배수가 1 보다 크면 그 에이전트의 표가 그만큼 무거워져요(최대 ×{r.max_skill}). 한 에이전트가 혼자 결정을 쥐지 않도록 범위를 잘라 둬요.
          채점은 끝난 거래일이 지난 판단만 세요 — 오늘 낸 입장은 내일부터 성적에 들어가요.
        </p>
      </article>
    </div>
  )
}
