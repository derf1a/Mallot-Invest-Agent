"""DB 스키마 단계 목록 — 서버 시작 시 app.core.db.init_db 가 PRAGMA user_version 이후 단계만 차례로 실행한다.

스키마를 바꿀 때는 항상 목록 끝에 새 단계를 더한다 (이미 실행된 단계는 고치지 않는다).
v1 은 Mallot(v22)의 투자 · 실행 기록 · LLM 사용량 · 시스템 요청 테이블을 그대로 옮긴 것이다
(실험실 테이블 이름만 lab_runs → runs, lab_metrics → run_metrics, lab_logs → run_logs).
"""

MIGRATIONS: list[str] = [
    # v1: Mallot 에서 옮긴 투자 에이전트 스키마
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE runs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        kind             TEXT    NOT NULL,              -- 작업 종류 (app.runs.runner 레지스트리 키)
        name             TEXT    NOT NULL,
        status           TEXT    NOT NULL DEFAULT 'queued'
                         CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
        params_json      TEXT    NOT NULL DEFAULT '{}',
        summary_json     TEXT,                          -- 끝난 뒤 결과 요약
        progress         REAL    NOT NULL DEFAULT 0,
        parent_id        INTEGER REFERENCES runs(id) ON DELETE SET NULL,   -- 이어 붙인 실행 (선택)
        artifact_path    TEXT,                          -- data 폴더 기준 상대 경로
        error            TEXT,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
        started_at       TEXT,
        finished_at      TEXT
    );
    CREATE INDEX idx_runs_kind ON runs (kind, id);
    CREATE TABLE run_metrics (
        run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        key    TEXT    NOT NULL,
        step   INTEGER NOT NULL,
        value  REAL    NOT NULL,
        PRIMARY KEY (run_id, key, step)
    );
    CREATE TABLE run_logs (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id  INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        ts      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')),
        level   TEXT    NOT NULL DEFAULT 'info' CHECK (level IN ('debug', 'info', 'warn', 'error')),
        message TEXT    NOT NULL
    );
    CREATE TABLE llm_usage (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')),
        feature            TEXT    NOT NULL,          -- agent_action / invest_assistant / invest_desk ...
        provider           TEXT    NOT NULL,          -- claude-agent-sdk (로컬 Claude Code 로그인 또는 API 키)
        model              TEXT    NOT NULL,
        input_tokens       INTEGER NOT NULL DEFAULT 0,
        output_tokens      INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd           REAL,                      -- SDK 가 알려주는 API 요금 환산액 (구독 로그인이면 실제 청구 아님)
        duration_ms        INTEGER,
        api_duration_ms    INTEGER,
        turns              INTEGER,
        ok                 INTEGER NOT NULL DEFAULT 1,
        error              TEXT,
        run_id             INTEGER REFERENCES runs(id) ON DELETE SET NULL
    );
    CREATE INDEX idx_llm_usage_created ON llm_usage (created_at);
    CREATE INDEX idx_llm_usage_feature ON llm_usage (feature, created_at);
    CREATE TABLE invest_agent_views (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        at         TEXT    NOT NULL,
        day        TEXT    NOT NULL,
        run_id     INTEGER,
        agent      TEXT    NOT NULL,
        symbol     TEXT    NOT NULL,
        stance     TEXT    NOT NULL CHECK (stance IN ('buy', 'sell')),
        confidence REAL    NOT NULL,
        price      REAL,
        UNIQUE (agent, symbol, day, stance)
    );
    CREATE INDEX idx_invest_agent_views_day ON invest_agent_views (day);
    CREATE TABLE invest_assistant_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
        role       TEXT    NOT NULL CHECK (role IN ('user', 'assistant', 'agent', 'event')),
        text       TEXT    NOT NULL,
        meta       TEXT    NOT NULL DEFAULT '{}'   -- JSON: 에이전트 키·이름, 실행 id, 운용 지시 id, 쓴 도구, 오류
    );
    CREATE TABLE invest_dart_corps (
        symbol       TEXT PRIMARY KEY,
        corp_code    TEXT NOT NULL,
        corp_name    TEXT NOT NULL DEFAULT '',
        modify_date  TEXT NOT NULL DEFAULT '',
        refreshed_at TEXT NOT NULL
    );
    CREATE TABLE invest_desk (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT    NOT NULL,                 -- 한국 시각 'YYYY-MM-DD HH:MM:SS'
        day        TEXT    NOT NULL,
        stage      TEXT    NOT NULL CHECK (stage IN ('market', 'sector', 'ideas', 'alert')),
        run_id     INTEGER,                          -- 리서치 실행(runs) — 경보는 없음
        data       TEXT    NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_invest_desk_stage ON invest_desk (stage, day);
    CREATE TABLE invest_external_holdings (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name  TEXT    NOT NULL DEFAULT '',    -- 증권사·계좌 이름 (예: 키움 ISA)
        symbol        TEXT    NOT NULL,
        name          TEXT    NOT NULL DEFAULT '',
        security_type TEXT    NOT NULL DEFAULT '',
        currency      TEXT    NOT NULL DEFAULT 'KRW',
        quantity      REAL    NOT NULL CHECK (quantity > 0),
        avg_price     REAL,
        memo          TEXT    NOT NULL DEFAULT '',
        updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    CREATE TABLE invest_fundamentals (
        symbol     TEXT    NOT NULL,
        bsns_year  INTEGER NOT NULL,
        reprt_code TEXT    NOT NULL,                  -- 11013 1분기 · 11012 반기 · 11014 3분기 · 11011 사업보고서
        fs_div     TEXT    NOT NULL,                  -- CFS 연결 · OFS 별도
        currency   TEXT    NOT NULL DEFAULT 'KRW',
        rcept_no   TEXT    NOT NULL DEFAULT '',       -- 앞 8자리가 공시 접수일 (그날 알 수 있던 값만 쓰는 시험에 쓴다)
        period_end TEXT    NOT NULL DEFAULT '',
        accounts   TEXT    NOT NULL,                  -- JSON: 계정 → 당기·전기·전전기·누적 금액
        fetched_at TEXT    NOT NULL,
        PRIMARY KEY (symbol, bsns_year, reprt_code)
    );
    CREATE TABLE invest_news (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        url_hash     TEXT    NOT NULL UNIQUE,
        url          TEXT    NOT NULL,
        title        TEXT    NOT NULL,
        source       TEXT    NOT NULL DEFAULT '',
        published_at TEXT,
        snippet      TEXT    NOT NULL DEFAULT '',
        query        TEXT    NOT NULL DEFAULT '',
        fetched_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    CREATE TABLE invest_news_links (
        news_id INTEGER NOT NULL REFERENCES invest_news(id) ON DELETE CASCADE,
        symbol  TEXT    NOT NULL,
        PRIMARY KEY (news_id, symbol)
    );
    CREATE INDEX idx_invest_news_links_symbol ON invest_news_links (symbol);
    CREATE TABLE invest_orders (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')),
        updated_at      TEXT,
        symbol          TEXT    NOT NULL,
        name            TEXT    NOT NULL DEFAULT '',
        currency        TEXT    NOT NULL DEFAULT 'KRW',
        side            TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
        order_type      TEXT    NOT NULL CHECK (order_type IN ('market', 'limit')),
        quantity        REAL    NOT NULL CHECK (quantity > 0),
        price           REAL,                -- 지정가 (시장가는 NULL)
        ref_price       REAL,                -- 주문 시점 현재가 (금액 한도 계산 기준)
        status          TEXT    NOT NULL CHECK (status IN
                        ('pending_approval', 'open', 'partial', 'filled', 'cancelled', 'rejected', 'failed')),
        filled_quantity REAL    NOT NULL DEFAULT 0,
        avg_fill_price  REAL,
        filled_at       TEXT,
        broker_order_id TEXT,                -- 토스증권 주문 id
        source          TEXT    NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'agent')),
        run_id          INTEGER REFERENCES runs(id) ON DELETE SET NULL,
        reason          TEXT    NOT NULL DEFAULT '',
        error           TEXT,
        checks_json     TEXT    NOT NULL DEFAULT '[]'
    );
    CREATE INDEX idx_invest_orders_status ON invest_orders (status, created_at);
    CREATE INDEX idx_invest_orders_run ON invest_orders (run_id);
    CREATE TABLE invest_score_rules (
        hash       TEXT PRIMARY KEY,                  -- 기준표 내용의 해시 — 기준이 바뀌면 자동으로 새 줄
        created_at TEXT NOT NULL,
        rules      TEXT NOT NULL                      -- JSON: 그때의 기준·가중치·구간표 전부
    );
    CREATE TABLE invest_score_runs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT    NOT NULL,                  -- 한국 시각 'YYYY-MM-DD HH:MM:SS'
        day        TEXT    NOT NULL,
        kind       TEXT    NOT NULL CHECK (kind IN ('sector', 'value', 'ideas', 'meeting')),
        run_id     INTEGER,                           -- 그 판단을 낸 실행(runs)
        regime     TEXT    NOT NULL DEFAULT '',
        rules_hash TEXT    NOT NULL REFERENCES invest_score_rules(hash),
        pool       INTEGER NOT NULL DEFAULT 0,
        picked     INTEGER NOT NULL DEFAULT 0,
        picked_by  TEXT    NOT NULL DEFAULT '',       -- llm · code
        summary    TEXT    NOT NULL DEFAULT '',
        notes      TEXT    NOT NULL DEFAULT '[]',
        meta       TEXT    NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_invest_score_runs ON invest_score_runs (kind, day);
    CREATE TABLE invest_score_rows (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        score_run_id INTEGER NOT NULL REFERENCES invest_score_runs(id) ON DELETE CASCADE,
        rank         INTEGER NOT NULL,
        subject      TEXT    NOT NULL,                -- 종목 코드 또는 섹터 id
        name         TEXT    NOT NULL DEFAULT '',
        score        REAL,
        value_score  REAL,
        price        REAL,                            -- 판단 때 가격 — 뒤에 수익률을 맞춰 볼 수 있게
        decision     TEXT    NOT NULL,                -- picked · passed · excluded · overweight · underweight
        pick_rank    INTEGER,
        reason       TEXT    NOT NULL DEFAULT '',
        sources      TEXT    NOT NULL DEFAULT '[]',
        detail       TEXT    NOT NULL DEFAULT '{}'    -- JSON: 지표·항목별 점수·가치평가 근거
    );
    CREATE INDEX idx_invest_score_rows_run ON invest_score_rows (score_run_id, rank);
    CREATE INDEX idx_invest_score_rows_subject ON invest_score_rows (subject, score_run_id);
    CREATE TABLE invest_universe (
        symbol        TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        market        TEXT NOT NULL,
        security_type TEXT NOT NULL DEFAULT '',
        refreshed_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    CREATE INDEX idx_invest_universe_name ON invest_universe (name);
    CREATE TABLE invest_watch_groups (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    INSERT INTO invest_watch_groups (id, name, sort_order) VALUES (1, '관심', 0);
    CREATE TABLE invest_watchlist (
        group_id      INTEGER NOT NULL REFERENCES invest_watch_groups(id) ON DELETE CASCADE,
        symbol        TEXT    NOT NULL,
        name          TEXT    NOT NULL DEFAULT '',
        market        TEXT    NOT NULL DEFAULT '',
        security_type TEXT    NOT NULL DEFAULT '',
        sort_order    INTEGER NOT NULL DEFAULT 0,
        added_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
        PRIMARY KEY (group_id, symbol)
    );
    CREATE INDEX idx_invest_watchlist_symbol ON invest_watchlist (symbol);
    CREATE TABLE system_issues (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        number      INTEGER UNIQUE,                  -- GitHub 이슈 번호, 아직 못 올렸으면 NULL
        url         TEXT    NOT NULL DEFAULT '',
        title       TEXT    NOT NULL,
        body        TEXT    NOT NULL DEFAULT '',
        status      TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
        by          TEXT    NOT NULL DEFAULT '',     -- 처음 알린 쪽
        reports     INTEGER NOT NULL DEFAULT 0,
        resolution  TEXT    NOT NULL DEFAULT '',
        created_at  TEXT    NOT NULL,
        updated_at  TEXT    NOT NULL,
        resolved_at TEXT
    );
    CREATE TABLE system_issue_events (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_id INTEGER NOT NULL REFERENCES system_issues(id) ON DELETE CASCADE,
        at       TEXT    NOT NULL,
        kind     TEXT    NOT NULL,                   -- opened · published · reported · resolved · reopened · imported
        who      TEXT    NOT NULL DEFAULT '',
        text     TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_system_issue_events_issue ON system_issue_events (issue_id, id);
    CREATE TABLE system_reports (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        at       TEXT    NOT NULL,
        last_at  TEXT    NOT NULL,
        by       TEXT    NOT NULL,
        source   TEXT    NOT NULL DEFAULT 'channel',  -- channel · minutes · assistant
        text     TEXT    NOT NULL,
        key      TEXT    NOT NULL UNIQUE,             -- 같은 말을 한 줄로
        seen     INTEGER NOT NULL DEFAULT 1,
        status   TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'linked', 'answered', 'ignored')),
        issue_id INTEGER REFERENCES system_issues(id) ON DELETE SET NULL,
        answer   TEXT    NOT NULL DEFAULT ''
    );
    """,
    # v2 (추적): 호출 추적 span — 실행 · 단계 · 에이전트 · LLM · 도구 · 사건 (app/core/trace.py)
    """
    CREATE TABLE trace_spans (
        id          TEXT    PRIMARY KEY,
        trace_id    TEXT    NOT NULL,                 -- 맨 위 span 의 id (실행 하나 · 비서 답 하나)
        parent_id   TEXT,
        run_id      INTEGER REFERENCES runs(id) ON DELETE CASCADE,
        kind        TEXT    NOT NULL CHECK (kind IN ('run', 'step', 'agent', 'llm', 'tool', 'event')),
        name        TEXT    NOT NULL,
        agent       TEXT,                             -- 이 span 을 일으킨 에이전트 키 (부모에게서 물려받음)
        status      TEXT    NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'error', 'cancelled')),
        start_ms    REAL    NOT NULL,                 -- epoch 밀리초
        end_ms      REAL,
        duration_ms INTEGER,
        input_json  TEXT,
        output_json TEXT,
        attrs_json  TEXT    NOT NULL DEFAULT '{}',
        error       TEXT
    );
    CREATE INDEX idx_trace_spans_trace ON trace_spans (trace_id, start_ms);
    CREATE INDEX idx_trace_spans_run ON trace_spans (run_id);
    CREATE INDEX idx_trace_spans_start ON trace_spans (start_ms);
    """,
    # v3 (거래 기록): 매도할 때의 토스 보유 평단 — 앱을 쓰기 전부터 갖고 있던 주식도 실현 손익을 낼 수 있게 (app/invest/ops/trades.py)
    """
    ALTER TABLE invest_orders ADD COLUMN basis_price REAL;
    """,
    # v4 (거래 기록): 토스 계좌의 지난 체결 — 앱을 쓰기 전 · 앱 밖에서 한 거래도 그때 가격으로 (app/invest/ops/trades.py import_broker)
    """
    CREATE TABLE invest_broker_fills (
        broker_order_id TEXT    PRIMARY KEY,
        symbol          TEXT    NOT NULL,
        side            TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
        order_type      TEXT    NOT NULL DEFAULT '',
        currency        TEXT    NOT NULL DEFAULT 'KRW',
        quantity        REAL    NOT NULL,             -- 체결 수량
        price           REAL,                         -- 체결 평균가
        amount          REAL,                         -- 체결 금액
        commission      REAL,
        tax             REAL,
        ordered_at      TEXT,
        filled_at       TEXT,
        status          TEXT    NOT NULL DEFAULT '',  -- 토스 상태 (FILLED · CANCELED 일부 체결 …)
        synced_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    CREATE INDEX idx_invest_broker_fills_at ON invest_broker_fills (filled_at);
    """,
]
