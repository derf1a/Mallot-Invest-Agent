/**
 * 에이전트 사람 — 다국적 이름·직함과 프로필 그림 정보(그림은 Face.tsx). 프로필은 포켓몬(전국도감 번호 → PokeAPI 공개 스프라이트,
 * 저장소에는 그림을 넣지 않는다), 불러오지 못하면(오프라인 등) 직접 그린 캐릭터. 직접 만든 에이전트는 키로 고정해서 고른다.
 */
import type { Agent, DeskTeam } from '../api'

export type Hair = 'short' | 'side' | 'long' | 'bun' | 'curly' | 'bob' | 'wavy'
export type Extra = 'glasses' | 'round' | 'headset' | 'tie' | 'earrings' | 'none'

export interface Look {
  bg: string
  skin: string
  hair: string
  style: Hair
  shirt: string
  extra: Extra
}

/** 화면에 쓰는 사람 정보 — 예전 세션의 시작 메시지에는 이름이 없어 직함으로 대신한다 */
export interface Person {
  key: string
  title: string
  name?: string | null
  full_name?: string | null
  country?: string | null
  /** 프로필 포켓몬 (전국도감 번호) — 없으면 키로 고른다 */
  pokemon?: number | null
  executor?: boolean
}

// 원래 Mallot 명단과 같은 번호 (비서 · 자문 · 시스템은 서버가 보내는 번호를 쓴다)
const POKEMON: Record<string, number> = {
  technical: 65, value: 143, news: 279, growth: 6, portfolio: 52, risk: 213, trader: 25, assistant: 113,
  strategist: 164, sector: 18, scout: 58,  // 운용 데스크 리서치 담당
  advisor: 282,                             // 자문 에이전트
}
const POKEMON_POOL = [1, 4, 7, 133, 39, 54, 94, 131, 196, 448, 658, 778]

export function pokemonOf(p: Person): number {
  return p.pokemon || POKEMON[p.key] || POKEMON_POOL[hash(p.key) % POKEMON_POOL.length]
}

/** PokeAPI 공개 스프라이트 저장소의 공식 일러스트 */
export const spriteUrl = (id: number) =>
  `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/${id}.png`

const LOOKS: Record<string, Look> = {
  technical: { bg: '#dbe8ff', skin: '#f1c9a5', hair: '#1f1f24', style: 'side', shirt: '#3b6fd8', extra: 'glasses' },   // 켄지
  value: { bg: '#efe2f5', skin: '#f6d7c3', hair: '#b9b4ac', style: 'bun', shirt: '#7a4b8f', extra: 'round' },          // 마거릿
  news: { bg: '#ffe6d6', skin: '#c68e63', hair: '#2a1a14', style: 'long', shirt: '#e0712f', extra: 'earrings' },       // 프리야
  growth: { bg: '#ffe1d6', skin: '#e8b892', hair: '#6b3e1f', style: 'short', shirt: '#d94a2b', extra: 'none' },        // 제이크
  portfolio: { bg: '#d9f2e8', skin: '#f3d2b8', hair: '#c9a46a', style: 'short', shirt: '#2f8f6f', extra: 'tie' },      // 루카스
  risk: { bg: '#fde0dc', skin: '#7a4a2e', hair: '#1b1310', style: 'curly', shirt: '#c8423b', extra: 'none' },          // 아마라
  trader: { bg: '#dfe6f0', skin: '#d9a27a', hair: '#2b1d16', style: 'wavy', shirt: '#24364f', extra: 'headset' },      // 디에고
  assistant: { bg: '#d8f3ee', skin: '#f5d6bd', hair: '#2a211d', style: 'bob', shirt: '#1f8f7a', extra: 'earrings' },   // 서윤
  strategist: { bg: '#e6e4fb', skin: '#f3d2b8', hair: '#8a5a2b', style: 'long', shirt: '#4b4fb0', extra: 'glasses' },  // 올리비아
  sector: { bg: '#dcefff', skin: '#f6d7c3', hair: '#c9a46a', style: 'short', shirt: '#2f6f9f', extra: 'none' },       // 닐스
  scout: { bg: '#fff1c7', skin: '#7a4a2e', hair: '#1b1310', style: 'curly', shirt: '#b07d12', extra: 'earrings' },    // 탄디
  advisor: { bg: '#e5f4e8', skin: '#f6d7c3', hair: '#6b3e1f', style: 'wavy', shirt: '#3e7d4f', extra: 'round' },     // 에밀리아
}
const SKINS = ['#f6d7c3', '#f1c9a5', '#d9a27a', '#c68e63', '#9a6440', '#7a4a2e']
const HAIRS = ['#1f1f24', '#4a3020', '#8a5a2b', '#c9a46a', '#b9b4ac']
const STYLES: Hair[] = ['short', 'side', 'long', 'bun', 'curly', 'bob', 'wavy']
const EXTRAS: Extra[] = ['none', 'glasses', 'tie', 'earrings', 'round', 'none']
const SHIRTS = ['#3b6fd8', '#2f8f6f', '#e0712f', '#7a4b8f', '#c8423b', '#24364f', '#b07d12']
const BGS = ['#dbe8ff', '#d9f2e8', '#ffe6d6', '#efe2f5', '#fde0dc', '#dfe6f0', '#fff1c7']

function hash(key: string): number {
  let h = 2166136261
  for (let i = 0; i < key.length; i++) h = Math.imul(h ^ key.charCodeAt(i), 16777619)
  return h >>> 0
}

export function lookOf(key: string): Look {
  if (LOOKS[key]) return LOOKS[key]
  const h = hash(key)
  const at = <T,>(list: T[], shift: number) => list[(h >>> shift) % list.length]
  return { bg: at(BGS, 0), skin: at(SKINS, 3), hair: at(HAIRS, 7), style: at(STYLES, 11), shirt: at(SHIRTS, 15), extra: at(EXTRAS, 19) }
}

/** 방 참여자 수 — 나 · 비서 · 자문 에이전트 · 리서치 데스크 · 회의 명단 */
export const memberCount = (people: Person[], desk?: DeskTeam | null) =>
  people.length + 2 + (desk?.advisor ? 1 : 0) + (desk?.people.length ?? 0)

/** 이름(없으면 직함) */
export const nameOf = (p: Person) => p.name || p.title

/** 시작 메시지의 참여자에 지금 명단의 이름을 채운다 (예전 세션) */
export function withNames<T extends Person>(people: T[], roster: Agent[] = []): T[] {
  return people.map((p) => {
    if (p.name) return p
    const r = roster.find((x) => x.key === p.key)
    return r ? { ...p, name: r.name, full_name: r.full_name, country: r.country, pokemon: p.pokemon ?? r.pokemon } : p
  })
}
