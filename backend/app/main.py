"""앱 조립 — 지금은 헬스 체크만. 기능은 다음 작업에서 붙인다."""

from fastapi import FastAPI

app = FastAPI(title="Mallot Invest Agent")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
