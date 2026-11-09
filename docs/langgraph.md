# LangGraph · LangSmith 와 연계할 수 있나

**결론: 된다.** 세 단계로 나눠서, 앞의 두 단계는 이미 만들었다.

| 단계 | 내용 | 상태 |
|---|---|---|
| 1. 구조 내보내기 | 흐름(회의 · 상시 운용 · 운용 데스크 · 비서)을 LangGraph `StateGraph` 로 컴파일 → `draw_mermaid()` | ✅ `GET /api/graph/langgraph/{flow}` |
| 2. 실제 호출 추적 | 우리 span 트리를 LangSmith run 트리로 그대로 보냄 | ✅ 선택 (`MIA_LANGSMITH_EXPORT=1`) |
| 3. 실행 엔진 교체 | 토론 세션을 LangGraph 노드로 나눠 실행 | 📝 계획 (아래) |

설치는 선택이다: `uv sync --extra langgraph --extra langsmith`. 설치하지 않아도 앱은 그대로 동작한다.

## 왜 추적을 직접 만들었나

- **데이터가 밖으로 나가지 않는다.** 프롬프트에는 보유 종목 · 평단 · 운용 목표가 들어간다. LangSmith 는 SaaS 라서 기본값으로 보내면 안 된다.
- **우리 호출은 LangChain Runnable 이 아니다.** Claude Agent SDK · MLX 작업 프로세스 · vLLM HTTP 를 직접 부르기 때문에, LangSmith 의 자동 추적(`LANGSMITH_TRACING=true`)이 잡지 못한다.
- 그래서 `app/core/trace.py` 가 SQLite(`trace_spans`)에 run tree 를 남기고, 원하면 `app/tracing/langsmith.py` 가 같은 트리를 `Client.create_run` 으로 보낸다.
  - 우리 span 은 자식이 먼저 끝나므로, 부모의 시작 시각 · id 를 DB 에서 읽어 `dotted_order` 를 직접 만든다. 그래서 순서와 상관없이 LangSmith 트리가 맞는다.
  - LLM run 에는 `usage_metadata`(토큰)와 `ls_provider` · `ls_model_name` 을 넣는다.

화면(Observe 메뉴)에서 보는 것은 LangSmith 와 같다:

- 트레이스 목록
- span 트리 + 시간 막대(워터폴)
- 입력 · 출력 · 토큰 · 요금
- 에이전트별 통계

그 위에 더 보이는 것:

- **에이전트 사이의 말** — 발언의 `responses[].to` 로 누가 누구에게 동의 · 반박했는지
- **코드 게이트 · 주문 사건**

## 지금 구조 ↔ LangGraph 개념

| 지금 (app/invest/debate) | LangGraph |
|---|---|
| `Session` (후보 · 게이트 · 주문 · 관리자 말) | 그래프 `State` (TypedDict) |
| 라운드: 분석 에이전트 동시 발언 (`asyncio.gather`) | 라운드 노드에서 `Send` 로 에이전트마다 퍼뜨리고 다시 모으기 |
| 라운드 2+ 반박 | 같은 라운드 노드를 조건부 선으로 다시 (`rounds` 만큼) |
| 게이트 (`session_gate`, 코드) | 조건부 선의 라우터 함수 — 열린 종목이 없으면 `END` |
| 트레이더 | 노드 (도구: `place_order`) |
| 관리자 채팅 · 승인 카드 | `interrupt()` + `Command(resume=...)` (사람이 끼어드는 자리) |
| 세션 기록 `messages.jsonl` | 체크포인터 (`InMemorySaver`, SQLite 는 `langgraph-checkpoint-sqlite` 를 따로 설치) |
| 상시 운용 채널 (장 내내 도는 루프) | 그래프 하나로는 어색하다. 계기마다 '대화 한 번' 그래프를 부르는 쪽이 맞다 |

## 3단계 계획 — 실행 엔진을 LangGraph 로

1. `agents.run_session` 을 함수 셋으로 나눈다: `analyst_round(state, rnd)` · `gate(state)` · `trader(state)`. 지금 엔진도 이 셋을 부르게 바꾼다 (동작 같음, 테스트 그대로).
2. `app/graph/langgraph_engine.py`: 이 셋을 노드로 한 `StateGraph`. 토론 설정에 `engine: native | langgraph`, 기본은 native.
3. 같은 가짜 LLM 각본으로 두 엔진의 결과(게이트 · 결정 · 주문)가 같은지 테스트한다.
4. 관리자 승인 카드를 `interrupt()` 로 옮길지는 그 다음에 정한다.

### 지켜야 할 것 (실계좌 주문)

- **주문 검사는 노드 안의 코드로 남긴다.** `place_order` 도구 검사와 `trading.place_order` 의 한도 · 잔고 · 비중 검사는 LangGraph 가 아니라 지금 코드가 한다.
- **주문 노드에는 `RetryPolicy` 를 걸지 않는다.** 노드를 다시 돌리면 같은 주문이 두 번 나갈 수 있다. 토스 주문 생성이 500 이면 재시도하지 않는 지금 규칙(`failed` + "토스 앱에서 확인")을 그대로 둔다.
- 체크포인트에서 이어 돌릴 때, 이미 낸 주문은 `invest_orders` 를 보고 건너뛴다 (`clientOrderId`).

## 참고

- LangGraph 문서: https://docs.langchain.com/oss/python/langgraph/overview
- LangSmith 트레이싱: https://docs.langchain.com/langsmith/observability
- 코드: [app/graph/langgraph_export.py](../backend/app/graph/langgraph_export.py) · [app/tracing/langsmith.py](../backend/app/tracing/langsmith.py) · [app/core/trace.py](../backend/app/core/trace.py)
