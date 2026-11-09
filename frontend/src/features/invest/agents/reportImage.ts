/**
 * 한 페이지 보고서 이미지 — 카드(Report.tsx)와 같은 재료(briefing.ts reportView)를 캔버스에 직접 그린다
 * (라이브러리 없이 — 폰 사파리에서도 같은 결과). 글자는 본문 글꼴(Pretendard)을 먼저 불러온 뒤 그린다. 언제나 라이트 색.
 */
import { niceTicks } from '../../../shared/chartUtils'
import type { Briefing } from '../api'
import { CASH } from '../portfolio/model'
import { HEAT_MAX, RATE_KEYS, REGIMES, axisPct, pct, pointLabel, reportView, toneOf, won, type Curve, type Goal, type Holding, type MeetingView, type Tone } from './briefing'

const W = 880
const PAD = 48
const COLORS = { bg: '#ffffff', ink: '#1e2328', muted: '#6b7280', rule: '#e5e7eb', accent: '#1f8f7a', soft: '#e8f6f2', up: '#d0312d', down: '#2563d9', warn: '#b26a00', warnSoft: '#fff4e0', tile: '#f6f7f9', track: '#e9ecef', upSoft: '#fdeceb', downSoft: '#e8effc' }
/** CSS 범주 색 → 라이트 값 (base.css 와 같은 값) */
const SERIES: Record<string, string> = { 'var(--series-1)': '#2a78d6', 'var(--series-2)': '#eb6834', 'var(--series-3)': '#1baf7a', 'var(--series-4)': '#eda100', 'var(--series-5)': '#e87ba4', 'var(--series-other)': '#898781' }
const FAMILY = '"Pretendard Variable", Pretendard, -apple-system, "Apple SD Gothic Neo", sans-serif'
const font = (weight: number, size: number) => `${weight} ${size}px ${FAMILY}`
const colorOf = (t: Tone) => (t === 'up' ? COLORS.up : t === 'down' ? COLORS.down : t === 'muted' ? COLORS.muted : t === 'warn' ? COLORS.warn : COLORS.ink)

function wrap(ctx: CanvasRenderingContext2D, text: string, width: number): string[] {
  const out: string[] = []
  for (const para of text.split('\n')) {
    let line = ''
    for (const word of para.split(/(\s+)/)) {
      const next = line + word
      if (ctx.measureText(next).width <= width || !line.trim()) {
        line = next
        continue
      }
      out.push(line.trimEnd())
      line = word.trimStart()
    }
    // 띄어쓰기 없이 긴 줄은 글자 단위로 자른다
    while (ctx.measureText(line).width > width) {
      let cut = line.length
      while (cut > 1 && ctx.measureText(line.slice(0, cut)).width > width) cut--
      out.push(line.slice(0, cut))
      line = line.slice(cut)
    }
    out.push(line)
  }
  return out
}

/** 보고서를 PNG 로 (2배 해상도). by = 쓴 사람 (예: '서윤 · 비서') */
export async function reportPng(r: Briefing, by: string): Promise<Blob> {
  const v = reportView(r)
  const m = v.market
  try {
    await Promise.all([400, 500, 600, 700].map((w) => document.fonts.load(font(w, 16), `${r.title} ${r.comment ?? ''} ${JSON.stringify(v)}`)))
  } catch {
    /* 글꼴을 못 불러오면 시스템 글꼴로 */
  }
  const scale = 2
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')!
  const inner = W - PAD * 2
  const RIGHT = PAD + inner

  // 그리기 도구
  const text = (s: string, x: number, y: number, f: string, color: string = COLORS.ink, align: CanvasTextAlign = 'left') => {
    ctx.font = f; ctx.fillStyle = color; ctx.textAlign = align; ctx.fillText(s, x, y); ctx.textAlign = 'left'
  }
  const box = (x: number, y: number, w: number, h: number, r2: number, color: string) => {
    ctx.fillStyle = color; ctx.beginPath(); ctx.roundRect(x, y, Math.max(w, 0), h, r2); ctx.fill()
  }
  const fit = (s: string, width: number, f: string) => {
    ctx.font = f
    if (ctx.measureText(s).width <= width) return s
    let cut = s.length
    while (cut > 1 && ctx.measureText(`${s.slice(0, cut)}…`).width > width) cut--
    return `${s.slice(0, cut)}…`
  }
  const lines = (s: string, width: number, f: string) => { ctx.font = f; return wrap(ctx, s, width) }

  // 1) 배치 (높이 계산)
  type Op = (y: number) => void
  const ops: { h: number; draw: Op }[] = []
  const add = (h: number, draw: Op) => ops.push({ h, draw })
  const heading = (title: string) => add(50, (y) => {
    text(title, PAD, y + 32, font(700, 18), COLORS.accent)
    ctx.fillStyle = COLORS.rule; ctx.fillRect(PAD, y + 42, inner, 1)
  })
  const label = (title: string) => add(30, (y) => text(title, PAD, y + 22, font(600, 14), COLORS.muted))
  const note = (s: string, tone: Tone = 'muted') => {
    const ls = lines(s, inner, font(400, 14))
    add(ls.length * 21 + 6, (y) => ls.forEach((l, i) => text(l, PAD, y + 18 + i * 21, font(400, 14), colorOf(tone))))
  }
  /** 칩 줄 — 넘치면 다음 줄로 */
  const chips = (items: { text: string; bg: string; color?: string }[]) => {
    const f = font(500, 14)
    ctx.font = f
    const rows: { x: number; w: number; it: (typeof items)[number] }[][] = [[]]
    let x = 0
    for (const it of items) {
      const w = ctx.measureText(it.text).width + 22
      if (x + w > inner && x > 0) { rows.push([]); x = 0 }
      rows[rows.length - 1].push({ x, w, it })
      x += w + 8
    }
    add(rows.length * 34 + 4, (y) => rows.forEach((row, k) => row.forEach((c) => {
      box(PAD + c.x, y + 4 + k * 34, c.w, 28, 14, c.it.bg)
      text(c.it.text, PAD + c.x + 11, y + 23 + k * 34, f, c.it.color ?? COLORS.ink)
    })))
  }
  const diverge = (cx: number, half: number, y: number, h: number, value: number | null, max: number) => {
    ctx.fillStyle = COLORS.rule; ctx.fillRect(cx, y - 3, 1, h + 6)
    if (value === null || value === 0) return
    const w = Math.max(Math.min(Math.abs(value) / max, 1) * half, 2)
    box(value > 0 ? cx + 1 : cx - w, y, w, h, 3, value > 0 ? COLORS.up : COLORS.down)
  }

  const curve = (c: Curve) => add(226, (y) => {
    const series = [
      { label: '내 계좌', color: '#2a78d6', pts: c.points.map((p, i): [number, number] => [i, p.rate]) },
      ...(c.index ? [{ label: c.index, color: '#eb6834', pts: c.points.flatMap((p, i): [number, number][] => (p.index === null ? [] : [[i, p.index]])) }] : []),
    ].filter((s) => s.pts.length)
    let lx = PAD
    for (const s of series) {
      ctx.fillStyle = s.color; ctx.fillRect(lx, y + 10, 18, 3)
      text(s.label, lx + 24, y + 16, font(500, 13), COLORS.muted)
      ctx.font = font(500, 13)
      lx += 24 + ctx.measureText(s.label).width + 18
    }
    const [L, R, T, B] = [PAD + 62, RIGHT - 76, y + 30, y + 196]
    const ys = series.flatMap((s) => s.pts.map((p) => p[1]))
    let [lo, hi] = [Math.min(...ys), Math.max(...ys)]
    const pad = (hi - lo) * 0.08 || 0.01
    lo -= pad; hi += pad
    const last = c.points.length - 1
    const sx = (x: number) => L + (last ? x / last : 0.5) * (R - L)
    const sy = (n: number) => T + (1 - (n - lo) / (hi - lo)) * (B - T)
    for (const t of niceTicks(lo, hi)) {
      ctx.fillStyle = Math.abs(t) < 1e-9 ? '#c5cad0' : COLORS.rule; ctx.fillRect(L, sy(t), R - L, 1)
      text(axisPct(t), L - 8, sy(t) + 4, font(400, 12), COLORS.muted, 'right')
    }
    for (const t of c.ticks) text(pointLabel(c.points[t].at).split(' ')[0], sx(t), B + 20, font(400, 12), COLORS.muted, 'center')
    const ends = series.map((s) => ({ s, x: sx(s.pts[s.pts.length - 1][0]), y: sy(s.pts[s.pts.length - 1][1]), v: s.pts[s.pts.length - 1][1] })).sort((a, b) => a.y - b.y)
    for (const s of series) {
      ctx.strokeStyle = s.color; ctx.lineWidth = 2.5; ctx.lineJoin = 'round'; ctx.lineCap = 'round'
      ctx.beginPath()
      s.pts.forEach((p, i) => (i ? ctx.lineTo(sx(p[0]), sy(p[1])) : ctx.moveTo(sx(p[0]), sy(p[1]))))
      ctx.stroke()
    }
    ends.forEach((e, i) => {
      ctx.fillStyle = COLORS.bg; ctx.beginPath(); ctx.arc(e.x, e.y, 6, 0, Math.PI * 2); ctx.fill()
      ctx.fillStyle = e.s.color; ctx.beginPath(); ctx.arc(e.x, e.y, 4, 0, Math.PI * 2); ctx.fill()
      const prev = i ? ends[i - 1].y : -Infinity
      if (e.y - prev < 16) e.y = prev + 16
      text(pct(e.v, 2), e.x + 10, e.y + 4, font(600, 13))
    })
  })

  const goal = (g: Goal) => add(54, (y) => {
    const at = (n: number) => PAD + Math.max(0, Math.min(1, (n - g.min) / (g.max - g.min))) * inner
    text(g.label, PAD, y + 18, font(500, 15))
    text(g.value, RIGHT, y + 18, font(700, 15), g.warn ? COLORS.warn : colorOf(g.tone), 'right')
    ctx.font = font(700, 15)
    text(g.note, RIGHT - ctx.measureText(g.value).width - 12, y + 18, font(400, 13), COLORS.muted, 'right')
    box(PAD, y + 28, inner, 10, 5, COLORS.track)
    const [zero, end] = [g.signed ? at(0) : PAD, at(g.actual)]
    box(Math.min(zero, end), y + 28, Math.abs(end - zero), 10, 5, g.signed ? (g.actual >= 0 ? COLORS.up : COLORS.down) : g.warn ? COLORS.warn : '#2a78d6')
    if (g.signed) { ctx.fillStyle = COLORS.muted; ctx.fillRect(at(0), y + 26, 1, 14) }
    ctx.fillStyle = COLORS.ink; ctx.fillRect(at(g.mark) - 1, y + 24, 2, 18)
  })

  const allocation = () => {
    const total = v.slices.reduce((a, s) => a + s.value, 0)
    add(46, (y) => {
      let x = PAD
      const gaps = 2 * (v.slices.length - 1)
      v.slices.forEach((s) => {
        const w = Math.max((s.value / total) * (inner - gaps), 3)
        if (s.key === CASH) {
          box(x, y + 8, w, 30, 5, COLORS.tile)
          ctx.save(); ctx.beginPath(); ctx.roundRect(x, y + 8, w, 30, 5); ctx.clip()
          ctx.strokeStyle = '#d5d9de'; ctx.lineWidth = 2
          for (let k = -30; k < w; k += 9) { ctx.beginPath(); ctx.moveTo(x + k, y + 38); ctx.lineTo(x + k + 30, y + 8); ctx.stroke() }
          ctx.restore()
        } else box(x, y + 8, w, 30, 5, SERIES[s.color] ?? COLORS.muted)
        x += w + 2
      })
    })
    const half = (inner - 28) / 2
    for (let k = 0; k < v.slices.length; k += 2) {
      add(28, (y) => v.slices.slice(k, k + 2).forEach((s, j) => {
        const x = PAD + j * (half + 28)
        if (s.key === CASH) { box(x, y + 7, 13, 13, 3, COLORS.tile); ctx.strokeStyle = '#c5cad0'; ctx.lineWidth = 1; ctx.strokeRect(x + 0.5, y + 7.5, 12, 12) } else box(x, y + 7, 13, 13, 3, SERIES[s.color] ?? COLORS.muted)
        text(fit(s.label, half - 170, font(400, 15)), x + 21, y + 19, font(400, 15))
        text(won(s.value), x + half, y + 19, font(400, 14), COLORS.muted, 'right')
        text(`${((s.value / total) * 100).toFixed(1)}%`, x + half - 92, y + 19, font(600, 15), COLORS.ink, 'right')
      }))
    }
  }

  const holdings = (rows: Holding[]) => {
    const weightMax = Math.max(...rows.map((h) => h.weight ?? 0), 0.01)
    const rateMax = Math.max(...rows.map((h) => Math.abs(h.rate ?? 0)), 0.02)
    const [wx, px, dx] = [PAD + 268, PAD + 470, RIGHT]
    add(28, (y) => {
      text('종목', PAD, y + 18, font(500, 13), COLORS.muted)
      text('비중', wx, y + 18, font(500, 13), COLORS.muted)
      text('손익률', px, y + 18, font(500, 13), COLORS.muted)
      text('오늘', dx, y + 18, font(500, 13), COLORS.muted, 'right')
    })
    for (const h of rows) {
      add(56, (y) => {
        ctx.fillStyle = COLORS.rule; ctx.fillRect(PAD, y, inner, 1)
        box(PAD, y + 15, 10, 10, 5, SERIES[h.color] ?? COLORS.muted)
        text(fit(h.name, 244, font(600, 16)), PAD + 18, y + 24, font(600, 16))
        text(fit(h.sub, 244, font(400, 13)), PAD + 18, y + 44, font(400, 13), COLORS.muted)
        box(wx, y + 15, 130, 12, 3, COLORS.track)
        if (h.weight !== null) box(wx, y + 15, Math.max((h.weight / weightMax) * 130, 2), 12, 3, SERIES[h.color] ?? COLORS.muted)
        text(h.weight === null ? '-' : `${(h.weight * 100).toFixed(1)}%`, wx + 188, y + 26, font(600, 15), COLORS.ink, 'right')
        diverge(px + 56, 52, y + 15, 12, h.rate, rateMax)
        text(pct(h.rate), px + 176, y + 26, font(600, 15), colorOf(toneOf(h.rate)), 'right')
        if (h.pnl !== null) text(`${h.pnl > 0 ? '+' : ''}${won(h.pnl)}`, px + 176, y + 44, font(400, 13), COLORS.muted, 'right')
        text(pct(h.day), dx, y + 26, font(600, 15), colorOf(toneOf(h.day)), 'right')
      })
    }
  }

  const meeting = (x: MeetingView) => {
    const body = inner - 36
    const parts: { h: number; draw: Op }[] = []
    const para = (s: string, f: string, color: string, lh: number) => {
      const ls = lines(s, body, f)
      parts.push({ h: ls.length * lh + 4, draw: (y) => ls.forEach((l, i) => text(l, PAD + 18, y + lh - 5 + i * lh, f, color)) })
    }
    parts.push({ h: 28, draw: (y) => {
      text(x.at, PAD + 18, y + 19, font(500, 14), COLORS.muted)
      ctx.font = font(500, 14)
      text(fit(x.name, body - 170, font(600, 15)), PAD + 18 + ctx.measureText(x.at).width + 10, y + 19, font(600, 15))
      if (x.status) text(x.status, RIGHT - 18, y + 19, font(600, 13), x.live ? COLORS.warn : COLORS.muted, 'right')
    } })
    if (x.headline) para(x.headline, font(600, 16), COLORS.ink, 24)
    if (x.talks) para(x.talks, font(500, 14), COLORS.muted, 21)
    if (x.summary) para(x.summary, font(400, 14), COLORS.muted, 21)
    for (const b of x.board) {
      parts.push({ h: 26, draw: (y) => {
        const [bx, bw] = [PAD + 18 + 200, 240]
        const total = b.buy + b.sell || 1
        text(fit(b.name, 190, font(500, 14)), PAD + 18, y + 18, font(500, 14))
        box(bx, y + 8, bw, 12, 3, COLORS.track)
        if (b.buy) box(bx, y + 8, (b.buy / total) * bw - (b.sell ? 1 : 0), 12, 3, COLORS.up)
        if (b.sell) box(bx + (b.buy / total) * bw + (b.buy ? 1 : 0), y + 8, (b.sell / total) * bw - (b.buy ? 1 : 0), 12, 3, COLORS.down)
        text(`매수 ${b.buy} · 매도 ${b.sell}`, RIGHT - 18, y + 18, font(500, 14), COLORS.muted, 'right')
      } })
    }
    for (const o of x.orders) {
      parts.push({ h: 28, draw: (y) => {
        box(PAD + 18, y + 4, 44, 22, 6, o.buy ? COLORS.upSoft : COLORS.downSoft)
        text(o.side, PAD + 40, y + 20, font(600, 13), o.buy ? COLORS.up : COLORS.down, 'center')
        text(fit(`${o.name} ${o.quantity}`, body - 260, font(500, 14)), PAD + 74, y + 20, font(500, 14))
        text(o.status, RIGHT - 110, y + 20, font(400, 13), COLORS.muted, 'right')
        if (o.change !== null) text(pct(o.change), RIGHT - 18, y + 20, font(600, 14), colorOf(toneOf(o.change)), 'right')
      } })
    }
    if (x.trader) para(`트레이더 · ${x.trader}`, font(400, 14), COLORS.muted, 21)
    for (const f of x.follow) para(`→ ${f}`, font(400, 14), COLORS.ink, 21)
    const h = parts.reduce((a, p) => a + p.h, 0) + 20
    add(h + 12, (y) => {
      box(PAD, y + 4, inner, h, 12, COLORS.tile)
      let at = y + 14
      for (const p of parts) { p.draw(at); at += p.h }
    })
  }

  const indicators = () => {
    const max = Math.max(...m.indicators.map((i) => Math.abs(i.rate ?? 0)), 0.005)
    for (const i of m.indicators) {
      add(32, (y) => {
        text(i.name, PAD, y + 21, font(500, 15))
        text(i.price, PAD + 300, y + 21, font(500, 15), COLORS.ink, 'right')
        diverge(PAD + 490, 150, y + 11, 12, i.rate, max)
        text(pct(i.rate, 2), RIGHT, y + 21, font(600, 15), colorOf(toneOf(i.rate)), 'right')
      })
    }
  }

  const regime = () => {
    const x = m.view!
    add(52, (y) => {
      const w = (inner - 8) / 3
      REGIMES.forEach((g, i) => {
        const on = g.key === x.key
        box(PAD + i * (w + 4), y + 8, w, 34, 8, on ? (g.key === 'risk_on' ? COLORS.upSoft : g.key === 'risk_off' ? COLORS.downSoft : COLORS.track) : COLORS.tile)
        text(g.label, PAD + i * (w + 4) + w / 2, y + 30, font(on ? 700 : 400, 14), on ? (g.key === 'risk_on' ? COLORS.up : g.key === 'risk_off' ? COLORS.down : COLORS.ink) : COLORS.muted, 'center')
      })
    })
    if (x.headline) {
      const ls = lines(x.headline, inner, font(600, 16))
      add(ls.length * 24 + 6, (y) => ls.forEach((l, i) => text(l, PAD, y + 20 + i * 24, font(600, 16))))
    }
    if (x.summary) note(x.summary)
    const tags = [...x.favor.map((t) => ({ text: `주목 ${t}`, bg: COLORS.soft })), ...x.avoid.map((t) => ({ text: `피할 것 ${t}`, bg: COLORS.warnSoft })),
      ...(x.cash ? [{ text: x.cash, bg: COLORS.tile, color: COLORS.muted }] : [])]
    if (tags.length) chips(tags)
  }

  const sectors = () => {
    const s = m.sectors!
    if (!s.rows.length) {
      chips([...s.over.map((t) => ({ text: `▲ ${t}`, bg: COLORS.soft })), ...s.under.map((t) => ({ text: `▼ ${t}`, bg: COLORS.tile, color: COLORS.muted }))])
      return
    }
    const [nw, cw] = [inner - 4 * 104, 104]
    add(28, (y) => {
      text('섹터', PAD, y + 18, font(500, 13), COLORS.muted)
      RATE_KEYS.forEach((k, i) => text(k.label, PAD + nw + i * cw + cw - 10, y + 18, font(500, 13), COLORS.muted, 'right'))
    })
    for (const row of s.rows) {
      add(34, (y) => {
        text(row.side === 'over' ? '▲' : '▼', PAD, y + 22, font(600, 13), row.side === 'over' ? COLORS.accent : COLORS.muted)
        text(fit(row.name, nw - 26, font(500, 15)), PAD + 20, y + 22, font(500, 15))
        row.rates.forEach((n, i) => {
          const x = PAD + nw + i * cw
          if (n !== null) {
            const [red, green, blue] = n >= 0 ? [208, 49, 45] : [37, 99, 217]
            box(x + 2, y + 3, cw - 4, 28, 5, `rgba(${red},${green},${blue},${(Math.min(Math.abs(n) / HEAT_MAX, 1) * 0.42 + 0.04).toFixed(3)})`)
          }
          text(n === null ? '-' : pct(n), x + cw - 10, y + 22, font(500, 14), COLORS.ink, 'right')
        })
      })
    }
  }

  const news = () => {
    for (const n of m.news) {
      const f = font(400, 15)
      const tag = n.tag ? `${n.tag} · ` : ''
      const ls = lines(`${tag}${n.title}`, inner - 130, f)
      add(ls.length * 23 + 8, (y) => {
        ctx.fillStyle = COLORS.rule; ctx.fillRect(PAD, y, inner, 1)
        ls.forEach((l, i) => text(l, PAD, y + 21 + i * 23, f))
        if (n.source) text(fit(n.source, 118, font(400, 13)), RIGHT, y + 21, font(400, 13), COLORS.muted, 'right')
      })
    }
  }

  // 머리
  add(6, () => undefined)
  add(44, (y) => text(r.title, PAD, y + 32, font(700, 30)))
  add(30, (y) => text(`${r.created_at} · ${by}${r.source === 'sample' ? ' · 샘플 시세' : ''}`, PAD, y + 18, font(400, 15), COLORS.muted))
  if (r.comment) {
    const ls = lines(r.comment, inner - 36, font(500, 17))
    const h = ls.length * 27 + 30
    add(h + 18, (y) => {
      box(PAD, y + 8, inner, h, 14, COLORS.soft)
      ctx.fillStyle = COLORS.accent; ctx.fillRect(PAD, y + 8, 5, h)
      ls.forEach((l, i) => text(l, PAD + 22, y + 8 + 33 + i * 27, font(500, 17)))
    })
  }
  if (v.kpis.length) {
    const gap = 12
    const tw = (inner - gap * (v.kpis.length - 1)) / v.kpis.length
    add(112, (y) => v.kpis.forEach((k, i) => {
      const x = PAD + i * (tw + gap)
      box(x, y + 16, tw, 86, 12, COLORS.tile)
      text(k.label, x + 16, y + 40, font(500, 13), COLORS.muted)
      text(k.value, x + 16, y + 68, font(700, tw < 180 ? 18 : 21), colorOf(k.tone))
      if (k.sub) text(k.sub, x + 16, y + 90, font(400, 13), k.tone && !k.subMuted ? colorOf(k.tone) : COLORS.muted)
    }))
  }
  if (v.warnings.length) {
    heading('주의')
    v.warnings.forEach((w) => note(w, 'warn'))
  }
  if (v.curve || v.goals.length) {
    heading('수익률')
    if (v.curve) curve(v.curve)
    if (v.goals.length) label('목표 대비')
    v.goals.forEach(goal)
  }
  heading('자산 배분·보유 종목')
  if (v.slices.length) allocation()
  if (v.holdings.length) {
    add(10, () => undefined)
    holdings(v.holdings)
  }
  v.notes.forEach((s) => note(s))
  heading('회의 요약')
  v.meetings.forEach(meeting)
  if (!v.meetings.length) note('아직 회의가 없어요')
  heading('장내 이슈')
  m.alerts.forEach((a) => chips([{ text: `경보 · ${a.length > 70 ? `${a.slice(0, 69)}…` : a}`, bg: COLORS.warnSoft }]))
  if (m.note) note(m.note, 'warn')
  if (m.indicators.length) indicators()
  if (m.view) { label(`시장 국면${m.view.by ? ` · ${m.view.by}` : ''}`); regime() }
  if (m.sectors) { label('섹터 비중 의견'); sectors() }
  if (m.news.length) { label('뉴스'); news() }
  add(56, (y) => {
    ctx.fillStyle = COLORS.rule; ctx.fillRect(PAD, y + 20, inner, 1)
    text('Mallot · 숫자는 코드가 계산 · 에이전트 판단은 수익을 보장하지 않아요', PAD, y + 42, font(400, 12), COLORS.muted)
  })

  // 2) 그리기
  const H = ops.reduce((a, o) => a + o.h, 0) + PAD - 10
  canvas.width = W * scale
  canvas.height = H * scale
  ctx.scale(scale, scale)
  ctx.fillStyle = COLORS.bg; ctx.fillRect(0, 0, W, H)
  ctx.fillStyle = COLORS.accent; ctx.fillRect(0, 0, W, 8)
  ctx.textBaseline = 'alphabetic'
  let y = PAD - 18
  for (const o of ops) {
    o.draw(y)
    y += o.h
  }
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('이미지를 만들지 못했어요'))), 'image/png'))
}

/** 저장 — 폰(터치)은 공유 시트(사진에 저장), PC 는 내려받기 */
export async function saveImage(blob: Blob, filename: string): Promise<void> {
  const file = new File([blob], filename, { type: 'image/png' })
  const touch = window.matchMedia?.('(pointer: coarse)').matches
  if (touch && navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: filename })
      return
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return
    }
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
