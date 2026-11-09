/** API 요청 공통 — JSON·FormData 본문, 실패하면 서버의 detail 문구로 Error. 기능별 api.ts 가 이걸로 엔드포인트를 묶는다. */
export async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'Content-Type': 'application/json' }
  }
  const res = await fetch(url, init)
  if (!res.ok) {
    let message = `요청 실패 (${res.status})`
    try {
      const data = await res.json()
      if (typeof data.detail === 'string') message = data.detail
    } catch {
      /* 본문이 JSON이 아니면 기본 메시지 */
    }
    throw new Error(message)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

/** 쿼리 문자열 — 빈 값(undefined·null·'')은 뺀다. 남는 게 없으면 '' */
export const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  return entries.length ? `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString()}` : ''
}
