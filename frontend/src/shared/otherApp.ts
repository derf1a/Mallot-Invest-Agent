/**
 * 짝 앱(Mallot Ledger ↔ Mallot Invest) 주소 — 지금 주소에서 만든다. 도메인을 코드에 적지 않는다.
 * ledger.<도메인> ↔ invest.<도메인>, 이 컴퓨터에서는 포트 8200 ↔ 8100 · 3200 ↔ 3100. 모르면 null (링크를 숨긴다).
 * 로그인은 앱마다 따로라, 처음 넘어가면 그쪽에서 한 번 로그인한다.
 */
const PAIR: Record<string, string> = { ledger: 'invest', invest: 'ledger' }
const PORTS: Record<string, string> = { '8200': '8100', '8100': '8200', '3200': '3100', '3100': '3200' }

export function otherAppUrl(here: Location = window.location): string | null {
  const [first, ...rest] = here.hostname.split('.')
  if (PAIR[first] && rest.length) return `${here.protocol}//${[PAIR[first], ...rest].join('.')}/`
  if ((here.hostname === 'localhost' || here.hostname === '127.0.0.1') && PORTS[here.port]) {
    return `${here.protocol}//${here.hostname}:${PORTS[here.port]}/`
  }
  return null
}

