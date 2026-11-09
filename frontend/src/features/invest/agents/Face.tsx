/**
 * 에이전트 캐릭터 프로필 그림(SVG)과 이름 줄. 그림은 배경 + 어깨(옷) + 얼굴 + 머리 모양 + 눈·웃는 입 + 소품(안경·헤드셋·넥타이·귀걸이)으로,
 * 20~40px 에서도 알아보게 단순하게. 모습·이름 정보는 people.ts.
 */
import type { ReactNode } from 'react'
import { lookOf, nameOf, type Look, type Person } from './people'

const INK = '#2b2522'

function HairBack({ l }: { l: Look }) {
  if (l.style === 'long') return <path d="M9.5 19C9.5 8 15 4.5 20 4.5S30.5 8 30.5 19V31C27 29 13 29 9.5 31Z" fill={l.hair} />
  if (l.style === 'bob') return <path d="M10.5 18C10.5 8.5 15 5 20 5S29.5 8.5 29.5 18V25.5C26 24 14 24 10.5 25.5Z" fill={l.hair} />
  if (l.style === 'wavy') return <path d="M11 17C11 8.5 15.5 5.5 20 5.5S29 8.5 29 17C29 20 27.5 21 27 19.5 26 21 13 21 13 19.5 12.5 21 11 20 11 17Z" fill={l.hair} />
  return null
}

function HairTop({ l }: { l: Look }) {
  switch (l.style) {
    case 'short':
      return <path d="M11.8 15.5C11.5 8.5 15.5 6 20 6S28.6 8.5 28.2 15.5C26.5 11.5 23.5 10.5 20 10.5S13.5 11.5 11.8 15.5Z" fill={l.hair} />
    case 'side':
      return <path d="M11.6 16C11.2 8.5 15.5 5.8 20.5 5.8 25.5 5.8 28.8 9 28.4 15.5 26 12 21 10.5 15.5 11.8 14 12.4 12.6 13.8 11.6 16Z" fill={l.hair} />
    case 'bun':
      return (
        <>
          <circle cx="20" cy="5.2" r="3.4" fill={l.hair} />
          <path d="M11.8 15.5C11.8 9 15.5 6.6 20 6.6S28.2 9 28.2 15.5C26 11.8 23 11 20 11S14 11.8 11.8 15.5Z" fill={l.hair} />
        </>
      )
    case 'curly':
      return (
        <g fill={l.hair}>
          {[[13, 11], [16.5, 8], [20.5, 7], [24.5, 8], [27.5, 11.5], [12, 14.5], [28.2, 15]].map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r="3.6" />)}
        </g>
      )
    case 'long':
    case 'bob':
      return <path d="M11.8 16C11.8 9.5 15.5 6.4 20 6.4S28.2 9.5 28.2 16C25.5 12.5 23.5 11.6 20 11.2 16.5 11.6 14.5 12.5 11.8 16Z" fill={l.hair} />
    case 'wavy':
      return <path d="M11.8 15.5C11.8 9 15.5 6.2 20 6.2S28.2 9 28.2 15.5C27 13 25.5 12.6 24.5 13.5 23.5 11.8 21.5 11.5 20 12.6 18.5 11.5 16.5 11.8 15.5 13.5 14.5 12.6 13 13 11.8 15.5Z" fill={l.hair} />
  }
}

function Extra({ l }: { l: Look }) {
  switch (l.extra) {
    case 'glasses':
      return (
        <g fill="none" stroke={INK} strokeWidth="0.9">
          <rect x="13.6" y="15.6" width="5.2" height="3.6" rx="1.2" />
          <rect x="21.2" y="15.6" width="5.2" height="3.6" rx="1.2" />
          <path d="M18.8 17.2H21.2" />
        </g>
      )
    case 'round':
      return (
        <g fill="none" stroke={INK} strokeWidth="0.8">
          <circle cx="16.6" cy="17.5" r="2.4" />
          <circle cx="23.4" cy="17.5" r="2.4" />
          <path d="M19 17.3H21" />
        </g>
      )
    case 'headset':
      return (
        <g fill="none" stroke={INK} strokeWidth="1.3" strokeLinecap="round">
          <path d="M11 17C11 10 15 6.8 20 6.8S29 10 29 17" />
          <rect x="9.6" y="15.6" width="2.6" height="4.6" rx="1.2" fill={INK} />
          <path d="M11 20.5C11.5 23.5 14 24.6 16.5 24.2" strokeWidth="0.9" />
        </g>
      )
    case 'tie':
      return <path d="M20 28.2L21.4 29.6 20.6 35.5 20 36.4 19.4 35.5 18.6 29.6Z" fill="#e8c24a" />
    case 'earrings':
      return (
        <g fill="#e8c24a">
          <circle cx="11.9" cy="21" r="1" />
          <circle cx="28.1" cy="21" r="1" />
        </g>
      )
    default:
      return null
  }
}

/** 직접 그린 캐릭터 (정사각, 부모 크기에 맞춤) */
export function Face({ agentKey }: { agentKey: string }) {
  const l = lookOf(agentKey)
  return (
    <svg viewBox="0 0 40 40" width="100%" height="100%" aria-hidden focusable="false">
      <rect width="40" height="40" fill={l.bg} />
      <HairBack l={l} />
      <path d="M5 40C5 31.5 12 27.4 20 27.4S35 31.5 35 40Z" fill={l.shirt} />
      <path d="M17.2 23.5H22.8V28.6C21 30 19 30 17.2 28.6Z" fill={l.skin} />
      <ellipse cx="20" cy="17.4" rx="8.2" ry="9" fill={l.skin} />
      <HairTop l={l} />
      <circle cx="16.8" cy="17.6" r="1.05" fill={INK} />
      <circle cx="23.2" cy="17.6" r="1.05" fill={INK} />
      <path d="M17.6 21.4Q20 23.4 22.4 21.4" fill="none" stroke={INK} strokeWidth="0.95" strokeLinecap="round" />
      <Extra l={l} />
    </svg>
  )
}

/** 프로필 그림 칸 — 채팅 말풍선 옆 40px 기본, className 으로 크기 바꿈. title 은 마우스를 올리면 보이는 이름·직함·국적 */
export function AgentAvatar({ p, className = '', label = true, children }: { p: Person; className?: string; label?: boolean; children?: ReactNode }) {
  const tip = [p.name && p.full_name && p.full_name !== p.name ? `${p.name} (${p.full_name})` : nameOf(p), p.title, p.country]
    .filter(Boolean).join(' · ')
  return (
    <span className={`msg-avatar face${p.executor ? ' executor' : ''}${className ? ` ${className}` : ''}`} title={label ? tip : undefined}
      style={{ background: lookOf(p.key).bg }} aria-hidden>
      <Face agentKey={p.key} />
      {children}
    </span>
  )
}

/** 말풍선 위 이름 줄 — 이름 · 직함 */
export function NameLine({ p }: { p: Person }) {
  return (
    <span className="msg-name">
      <b>{nameOf(p)}</b>
      {p.name && <span className="msg-role">{p.title}</span>}
    </span>
  )
}
