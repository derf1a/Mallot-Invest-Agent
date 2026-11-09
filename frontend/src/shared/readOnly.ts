/**
 * 읽기 전용(손님) 화면 — 관리자 화면을 그대로 보되 바꾸지 못한다.
 * 쓰기 입력(대화창 · 주문 폼 · 자동 운용 스위치 · 설정)은 이 값을 보고 감추거나 잠근다. 서버도 손님의 쓰기 요청을 403 으로 막는다.
 */
import { createContext, useContext } from 'react'

export const ReadOnlyContext = createContext(false)
export const useReadOnly = () => useContext(ReadOnlyContext)
