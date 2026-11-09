# Mallot Invest Agent — 개발 안내

Mallot(개인 자산관리 앱)의 투자 에이전트만 떼어 낸 공개 저장소. 사용 설명은 [README.md](README.md), 구조는 [docs/architecture.md](docs/architecture.md).

## 규칙

- **공개 저장소다.** 실제 거래 · 계좌번호 · API 키 · DB · `data/` 는 절대 커밋하지 않는다.
  - 커밋 훅(`make hooks`)과 CI 의 `scripts/check-secrets.sh` 가 막는다.
  - 샘플이 필요하면 가짜 데이터를 만든다.
- **커밋 · PR 에 Claude 공동 작성자 줄이나 "Generated with" 줄을 넣지 않는다.**
  - 기여자는 저장소 주인만 나와야 한다.
  - 이 컴퓨터에서는 `.claude/settings.local.json` 의 `attribution` 이 빈 값이다.
- 작업 하나 = 브랜치 하나(`feat/` · `fix/` · `refactor/` · `docs/` · `ci/`) → 작은 커밋(Conventional Commits) → PR → CI 통과 → merge commit 으로 머지.
- 커밋 전에 `make test`(비밀값 · pytest · oxlint · tsc · vite build).
- LLM 을 Claude Code 로 실제로 불러 확인하는 스크립트 · 데모 서버는 커밋하지 않는다 (scratchpad 에만). 가짜 LLM 을 쓰는 pytest 는 괜찮다.
- 주석 · 화면 글은 한국어.

## 실행

| 명령 | 내용 |
|---|---|
| `make install` | uv · npm 의존성 + 커밋 훅 |
| `make dev` | 백엔드 :8100(--reload) + 프론트 :3100 |
| `make serve` | 프론트 빌드 → 백엔드 하나로 :8100 (기본 127.0.0.1) |
| `make test` | 비밀값 검사 + pytest + 프론트 린트 · 타입 · 빌드 |

- 선택 설치: `cd backend && uv sync --extra langsmith --extra langgraph`.
- 환경 변수 접두사는 `MIA_` 다 (DB `MIA_DB_PATH`, 주문 PIN `MIA_TRADING_PIN`, `MIA_SERVICES=0` 이면 일정 스레드 끔, `MIA_TRACE=0` 이면 추적 끔).

## 구조 규칙 (tests/test_architecture.py)

- 공용 계층 `core` · `llm` · `runs` 는 기능을 모듈 맨 위에서 불러오지 않는다.
- `invest` 바닥 계층(`broker` · `marketdata` · `analysis`)은 에이전트 계층(`debate` · `ops` · `advice`)을 맨 위에서 불러오지 않는다.
- 라우터(`api*.py`)는 다른 모듈이 불러오지 않는다. 같이 쓸 함수는 로직 모듈에 둔다.
- 스키마는 `app/core/migrations.py` 끝에 새 단계로만 더한다.

## 지켜야 할 것 (실계좌)

- 모든 주문은 `invest/broker/trading.place_order` 한 곳을 지난다. 검사를 우회하는 주문 경로를 만들지 않는다.
- 토스 주문 생성이 500 이나 연결 오류면 다시 시도하지 않는다. 접수 여부를 알 수 없어서다.
- 새 LLM 호출은 `llm.client.structured` · `agentic` 으로만 부른다. 에이전트가 부르면 `routes.as_agent(key)` 로 감싼다 — 경로 고르기와 추적이 같이 따라온다.
- 새 에이전트 · 흐름을 더하면 `app/graph/catalog.py` · `topology.py` 에도 넣어 Agents · Graph 화면에 보이게 한다.
