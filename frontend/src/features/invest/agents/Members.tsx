/**
 * 참여자 서랍 — 채팅방 왼쪽 위 참여자 수를 누르면 오른쪽에서 열린다(카카오톡 대화 상대 목록처럼).
 * 나(관리자) · 비서 · 리서치 데스크(시장 전략가·섹터 애널리스트·종목 발굴) · 투자위원회(지금 명단의 에이전트: 이름 · 직함 · 전체 이름 ·
 * 국적 · 성향, 함께 맡는 일 — 안건·리스크 감시·집행) — 방(투자 회의실)의 참여자라 지난 회의를 보고 있어도 지금 팀을 보여 준다.
 * 그 회의에 누가 있었는지·입장표·게이트는 '현황' 서랍과 회의록에.
 */
import type { Agent, DeskTeam } from '../api'
import { AgentAvatar } from './Face'
import { memberCount, nameOf, type Person } from './people'

export default function Members({ people, assistant, desk, roster, onClose }: {
  people: Person[]
  assistant?: Person
  desk?: DeskTeam | null
  roster: Agent[]
  onClose: () => void
}) {
  const me: Person = assistant ?? { key: 'assistant', title: '비서' }
  const count = memberCount(people, desk)
  return (
    <>
      <div className="room-drawer-backdrop" onClick={onClose} />
      <aside className="room-drawer" aria-label="참여자">
        <div className="room-drawer-head">
          <h2>참여자 <span className="muted small">{count}</span></h2>
          <button className="icon-button" aria-label="닫기" onClick={onClose}>✕</button>
        </div>
        <div className="room-drawer-body scroll-y">
          <ul className="member-list">
            <li>
              <span className="msg-avatar me" aria-hidden>나</span>
              <span className="member-text"><b>나 <span className="msg-role">관리자</span></b><span className="muted small">의견·운용 지시를 보내고 주문을 승인해요</span></span>
            </li>
            <Member p={me} note="내 말에 답하고, 다른 에이전트에게 묻고, 운용 목표·지시를 정리하고, 회의 일정·안건을 정하고, 회의록을 써요" />
            {desk?.advisor && <Member p={desk.advisor} note={desk.advisor.role} />}
            {desk?.system && <Member p={desk.system} note={desk.system.role} />}
          </ul>
          {desk && desk.people.length > 0 && (
            <>
              <h3 className="member-group">리서치 데스크 <span className="muted small">회의 전에 시장·섹터·종목을 훑어요</span></h3>
              <ul className="member-list">{desk.people.map((p) => <Member key={p.key} p={p} note={p.role} />)}</ul>
            </>
          )}
          <h3 className="member-group">투자위원회 <span className="muted small">토론하고 주문해요</span></h3>
          <ul className="member-list">
            {people.map((a) => (
              <Member key={a.key} p={a} duty={desk?.duties[a.key]}
                note={roster.find((r) => r.key === a.key)?.persona ?? (a.executor ? '열린 종목만 주문을 제안해요' : '분석 에이전트')} />
            ))}
          </ul>
        </div>
      </aside>
    </>
  )
}

function Member({ p, note, duty }: { p: Person; note: string; duty?: string }) {
  return (
    <li>
      <AgentAvatar p={p} />
      <span className="member-text">
        <b>{nameOf(p)} <span className="msg-role">{p.name ? p.title : ''}{p.executor ? `${p.name ? ' · ' : ''}실행 담당` : ''}</span></b>
        {p.full_name && <span className="muted small">{p.full_name}{p.country ? ` · ${p.country}` : ''}</span>}
        {duty && <span className="small member-duty">{duty}</span>}
        <span className="muted small member-persona">{note}</span>
      </span>
    </li>
  )
}
