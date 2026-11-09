# 인터넷에 열기 · 휴대폰에서 쓰기

관리자(나)는 어디서든 비밀번호로 로그인해 모든 화면을 쓰고, 손님은 공유 링크로 **운용 상황 · 보고서만** 읽는다.

| 누가 | 들어오는 길 | 볼 수 있는 것 |
|---|---|---|
| 관리자 | 주소 → 비밀번호 로그인 (7일 유지) | 모든 화면 · 설정 · 주문(주문은 PIN 도 따로) |
| 손님 | 공유 링크 `…/share?k=키` (30일 유지) | 자동 운용 상태 · 오늘 일정 · 오늘 리서치 · 지금 보고서 · 지난 보고서 · 회의 목록 · 회의방 대화 (금액 포함, 읽기만) |
| 그 밖 | — | 로그인 화면만. API 는 401 |

손님에게 보이지 않는 것: 운용 지시 글(개수만), 비서와 나눈 대화, 설정 · 키 · 한도, 실행 로그 · 추적, 에이전트 프롬프트.

## 1. 관리자 비밀번호

```bash
make admin-password      # 10자 이상, .env 에 scrypt 해시(MIA_ADMIN_PASSWORD_HASH)로 저장 — 평문은 남지 않는다
```

비밀번호를 바꾸면 이전 로그인이 모두 끊긴다. 5번 틀리면 15분 잠긴다.

## 2. 띄우기

```bash
make share
```

1. 프론트를 빌드한다.
2. 백엔드를 `127.0.0.1:8100` 에 로그인 필수(`MIA_PUBLIC=1`)로 띄운다.
3. Cloudflare 터널을 연다.

내 도메인 설정이 없으면 계정 없이 쓰는 **임시 주소**(`https://…trycloudflare.com`)를 받는다. 이 주소는 다시 켤 때마다 바뀐다. 지금 주소는 `data/share-url.txt` 에 있다.

- 공유기 포트를 열 필요가 없다. 터널은 이 Mac 에서 Cloudflare 쪽으로 나가는 연결이다.
- 관리자 비밀번호 없이 터널을 거쳐 온 요청은 서버가 막는다 (`Cf-Connecting-IP` · `X-Forwarded-For` 를 보고 403).
- 앱이 도는 동안 Mac 이 잠들지 않게 한다: 시스템 설정 > 배터리(에너지) > "전원 어댑터 연결 시 자동으로 잠자기 방지", 또는 `caffeinate -s make share`.

## 3. 내 도메인으로 고정 주소

먼저 도메인을 Cloudflare 에 추가해 둔다 (네임서버가 Cloudflare 여야 한다).

```bash
cloudflared tunnel login                      # 브라우저에서 도메인을 고른다 → ~/.cloudflared/cert.pem
make tunnel-setup HOST=invest.example.com     # 터널 mallot-invest 만들기 + DNS + ~/.cloudflared/mallot-invest.yml
make share                                    # 이제 https://invest.example.com 으로 열린다
```

- 더 막고 싶으면 Cloudflare Zero Trust > Access 로 관리자 이메일만 들어오게 할 수도 있다.
- 그 경우 손님 링크(`/share`, `/api/share/*`, `/api/auth/share/enter`, `/api/auth/me`)는 Access 에서 빼야 손님이 들어온다.

## 4. 손님에게 공유

관리자 화면 **Settings > 공유 · 접근** 에서 한다.

1. **공유 켜기**
2. 링크 복사, 또는 QR 코드
3. 공유를 끝내려면 **공유 끄기** (바로 끊긴다) 나 **새 키 만들기** (이전 링크와 이미 들어온 손님이 모두 끊긴다)

## 5. 아이폰(크롬 · 사파리)에서

- **손님으로:** 공유 링크를 열면 아래쪽 탭(현황 · 보고서 · 회의)으로 나뉜 화면이 나온다.
- **관리자로:** 같은 주소(`https://…/`)를 열고 비밀번호로 로그인한다.
  - 손님으로 들어와 있었다면 위쪽 "관리자 로그인"을 누른다.
  - 휴대폰 화면에서는 메뉴가 왼쪽 위 ☰ 로 바뀐다.
- **앱처럼 쓰기:** 공유 버튼 → 홈 화면에 추가하면 아이콘이 생기고, 주소창 없이 앱처럼 열린다.
- **주문:** 관리자로 로그인해도 주문 PIN 은 따로 묻는다.

## 보안 메모

- 쿠키는 HttpOnly · SameSite=Strict, https 면 Secure. 서명 비밀은 `MIA_SESSION_SECRET` 또는 `data/session_secret`(권한 600, git 제외)에 둔다.
- 다른 사이트의 페이지가 이 앱으로 보내는 쓰기 요청은 Origin 검사로 막는다.
- 로그인 · 공유 · 접근 규칙은 [backend/app/auth](../backend/app/auth), 손님 데이터는 [backend/app/share/views.py](../backend/app/share/views.py) 에 있다.
- 손님 화면의 보고서는 60초 캐시라서, 손님이 여러 번 열어도 토스 API 를 반복해서 부르지 않는다.
