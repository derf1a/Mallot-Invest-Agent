"""로컬 LLM (mlx-lm, Apple Silicon) — 모델 내려받기, 작업 프로세스(app/llm/mlx_worker.py) 올리기·내리기, 호출.

- 모델 파일: data/models/<저장소 이름> (git 제외). 내려받기는 별도 프로세스(이어받기 가능, 취소하면 받은 데까지 남는다).
- 작업 프로세스는 이 서버만 부르는 127.0.0.1 포트 + 무작위 토큰. 모델이 메모리(약 8GB)를 계속 차지하므로
  설정 > LLM 에서 올리고 내린다. 로컬 LLM 을 고른 상태에서 호출이 오면 내려받아 둔 모델을 자동으로 올린다.
- 설정 키: llm_provider ('claude' | 'local'), llm_local ({model, thinking}).
"""

import asyncio
import atexit
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.core.db import connect, data_dir
from app.llm.client import LLMError
from app.llm.mlx_worker import GIB, THINKING, TOKEN_HEADER, gpu_limit, sysctl_int

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = ("claude", "local")
S_PROVIDER = "llm_provider"
S_LOCAL = "llm_local"

# 고를 수 있는 로컬 모델. bytes 는 내려받기 전 화면에 보여 줄 크기 (받기 시작하면 저장소 실제 크기로 바뀐다)
CATALOG: dict[str, dict] = {
    "qwen3.8-27b-2bit": {
        "repo": "lukaskremla/Qwen3.8-27B-2bit-MLX-TextOnly",
        "title": "Qwen3.8 27B · 2bit",
        "bytes": 8_430_000_000,
        "note": "27B 를 16GB 맥에 맞추려 2bit(RTN)로 줄인 모델. 짧은 질문엔 답하지만 시험에서 JSON 요약·도구 호출은 반복 루프에 빠져 "
                "실패했다. 출력 약 11 tok/s, KV 캐시 약 4.7만 토큰.",
    },
    "qwen3.5-9b-4bit": {
        "repo": "mlx-community/Qwen3.5-9B-4bit",
        "title": "Qwen3.5 9B · 4bit",
        "bytes": 5_980_000_000,
        "note": "작지만 4bit 라 형식을 잘 지킨다 — 시험에서 JSON 요약(18초)·도구 호출 에이전트(26초) 통과. 가중치 4.7GiB, "
                "KV 캐시 약 20만 토큰.",
    },
}
DEFAULT_LOCAL = {"model": "qwen3.8-27b-2bit", "thinking": "off"}
MARGIN_GB = float(os.environ.get("MIA_LOCAL_KV_MARGIN_GB", "1.0"))  # KV 예산에서 빼 둘 여유분
LOAD_TIMEOUT = 300
CHAT_TIMEOUT = 3600  # 요청은 작업 프로세스에서 줄을 서서 기다릴 수 있다

_lock = threading.Lock()
_worker: dict = {"proc": None, "port": None, "token": None, "key": None, "started_at": None}


# ---------- 설정 ----------

def _setting(key: str):
    try:
        conn = connect()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
        return json.loads(row["value"]) if row else None
    except Exception:  # noqa: BLE001 — 설정을 못 읽으면 기본값
        return None


def _put(key: str, value) -> None:
    conn = connect()
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def provider() -> str:
    value = _setting(S_PROVIDER)
    return value if value in PROVIDERS else "claude"


def set_provider(value: str) -> None:
    if value not in PROVIDERS:
        raise ValueError("claude 또는 local 만 고를 수 있어요")
    if value == "local" and not downloaded(options()["model"]):
        raise ValueError("로컬 모델을 먼저 내려받으세요")
    _put(S_PROVIDER, value)


def options() -> dict:
    raw = _setting(S_LOCAL) or {}
    model = raw.get("model") if raw.get("model") in CATALOG else DEFAULT_LOCAL["model"]
    thinking = raw.get("thinking") if raw.get("thinking") in THINKING else DEFAULT_LOCAL["thinking"]
    return {"model": model, "thinking": thinking}


def set_options(model: str | None = None, thinking: str | None = None) -> dict:
    cur = options()
    if model is not None:
        if model not in CATALOG:
            raise ValueError("목록에 없는 모델이에요")
        if provider() == "local" and not downloaded(model):
            raise ValueError("로컬 LLM 을 쓰는 중에는 내려받은 모델로만 바꿀 수 있어요")
        cur["model"] = model
    if thinking is not None:
        if thinking not in THINKING:
            raise ValueError(f"생각 모드는 {', '.join(THINKING)} 중 하나예요")
        cur["thinking"] = thinking
    _put(S_LOCAL, cur)
    return cur


def model_name(key: str | None = None) -> str:
    """사용량 기록에 남길 모델 이름 (저장소 이름)."""
    return CATALOG[key or options()["model"]]["repo"].split("/")[-1]


def model_title(key: str | None = None) -> str:
    return CATALOG[key or options()["model"]]["title"]


# ---------- 모델 파일 ----------

def models_dir() -> Path:
    # 다른 프로젝트(예: Mallot)에서 이미 받은 모델 폴더를 같이 쓰려면 MIA_MODELS_DIR
    custom = os.environ.get("MIA_MODELS_DIR")
    return Path(custom).expanduser() if custom else data_dir() / "models"


def model_dir(key: str) -> Path:
    return models_dir() / CATALOG[key]["repo"].replace("/", "__")


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _downloading(key: str) -> int | None:
    """내려받는 중이면 그 프로세스 pid (서버가 다시 떠도 pid 파일로 이어서 본다)."""
    pid_file = model_dir(key) / ".download.pid"
    try:
        pid = int(pid_file.read_text())
    except (OSError, ValueError):
        return None
    if _pid_alive(pid):
        return pid
    pid_file.unlink(missing_ok=True)
    return None


def downloaded(key: str) -> bool:
    d = model_dir(key)
    index = _read_json(d / "model.safetensors.index.json")
    weights = set((index.get("weight_map") or {}).values()) or {"model.safetensors"}
    return ((d / "config.json").exists() and all((d / w).exists() for w in weights)
            and not any(d.glob(".cache/huggingface/download/**/*.incomplete")) and _downloading(key) is None)


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def model_status(key: str) -> dict:
    d = model_dir(key)
    manifest = _read_json(d / ".download.json")
    total = manifest.get("total_bytes") or CATALOG[key]["bytes"]
    have = _dir_bytes(d)
    pid = _downloading(key)
    log = _tail(models_dir() / f"{key}.download.log")
    failed = pid is None and not downloaded(key) and "Traceback" in log
    return {
        "key": key, **{k: CATALOG[key][k] for k in ("repo", "title", "note")}, "path": str(d),
        "downloaded": downloaded(key), "downloading": pid is not None, "bytes": have, "total_bytes": total,
        "progress": min(1.0, have / total) if total else None, "error": log.splitlines()[-1][:300] if failed else None,
    }


def _tail(path: Path, n: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - n))
            return f.read().decode(errors="replace").strip()
    except OSError:
        return ""


def start_download(key: str) -> dict:
    if key not in CATALOG:
        raise ValueError("목록에 없는 모델이에요")
    if _downloading(key) or downloaded(key):
        return model_status(key)
    d = model_dir(key)
    d.mkdir(parents=True, exist_ok=True)
    log = open(models_dir() / f"{key}.download.log", "w")  # noqa: SIM115 — 자식 프로세스가 쓴다
    proc = subprocess.Popen([sys.executable, "-m", "app.llm.mlx_worker", "download", "--repo", CATALOG[key]["repo"],
                             "--dir", str(d)], cwd=BACKEND_ROOT, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    log.close()
    (d / ".download.pid").write_text(str(proc.pid))
    threading.Thread(target=proc.wait, daemon=True).start()  # 끝난 프로세스를 거둔다
    return model_status(key)


def cancel_download(key: str) -> dict:
    pid = _downloading(key)
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
        for _ in range(20):
            if not _pid_alive(pid):
                break
            time.sleep(0.1)
        (model_dir(key) / ".download.pid").unlink(missing_ok=True)
        with open(models_dir() / f"{key}.download.log", "a") as f:
            f.write("\n[취소됨 — 다시 내려받으면 이어서 받아요]\n")
    return model_status(key)


def delete_model(key: str) -> dict:
    if _worker["key"] == key and _running():
        raise ValueError("올라가 있는 모델은 지울 수 없어요 — 먼저 내리세요")
    if provider() == "local" and options()["model"] == key:
        raise ValueError("지금 쓰는 로컬 모델이에요 — 기본 LLM 을 Claude 로 바꾼 뒤 지우세요")
    cancel_download(key)
    shutil.rmtree(model_dir(key), ignore_errors=True)
    (models_dir() / f"{key}.download.log").unlink(missing_ok=True)
    return model_status(key)


# ---------- 작업 프로세스 ----------

def _running() -> bool:
    proc = _worker["proc"]
    return proc is not None and proc.poll() is None


def _request(path: str, body: dict | None = None, timeout: float = 2.0) -> dict:
    url = f"http://127.0.0.1:{_worker['port']}{path}"
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={TOKEN_HEADER: _worker["token"] or "",
                                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e)
        except (json.JSONDecodeError, OSError):
            detail = {}
        raise LLMError(detail.get("error") or f"로컬 LLM 오류 HTTP {e.code}") from None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load(key: str | None = None) -> dict:
    """작업 프로세스를 띄워 모델을 올리기 시작한다 (기다리지 않음 — 상태는 worker_status 로)."""
    key = key or options()["model"]
    if key not in CATALOG:
        raise ValueError("목록에 없는 모델이에요")
    if not downloaded(key):
        raise ValueError(f"{model_title(key)} 을(를) 먼저 내려받으세요")
    with _lock:
        if _running() and _worker["key"] == key:
            return worker_status()
        _stop()
        token = secrets.token_hex(16)
        port = _free_port()
        log = open(models_dir() / "worker.log", "w")  # noqa: SIM115 — 자식 프로세스가 쓴다
        proc = subprocess.Popen([sys.executable, "-m", "app.llm.mlx_worker", "serve", "--model", str(model_dir(key)),
                                 "--port", str(port), "--margin-gb", str(MARGIN_GB)],
                                cwd=BACKEND_ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env={**os.environ, "MIA_LOCAL_TOKEN": token})
        log.close()
        _worker.update(proc=proc, port=port, token=token, key=key, started_at=time.time())
    return worker_status()


def _stop() -> None:
    proc = _worker["proc"]
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    _worker.update(proc=None, port=None, token=None, key=None, started_at=None)


def unload() -> dict:
    with _lock:
        _stop()
    return worker_status()


atexit.register(unload)


def worker_status() -> dict:
    """stopped / starting(프로세스는 떴고 응답 전) / loading / ready / error."""
    proc = _worker["proc"]
    if proc is None:
        return {"state": "stopped"}
    base = {"model": _worker["key"], "pid": proc.pid, "uptime_sec": int(time.time() - (_worker["started_at"] or 0))}
    if proc.poll() is not None:
        tail = _tail(models_dir() / "worker.log").splitlines()
        return {**base, "state": "error", "error": f"작업 프로세스가 끝났어요 (코드 {proc.returncode}) {tail[-1] if tail else ''}"}
    try:
        health = _request("/health", timeout=1.5)
    except (LLMError, OSError):
        return {**base, "state": "starting"}
    return {**base, **health, "model": _worker["key"]}


async def ensure_ready() -> None:
    key = options()["model"]
    if not (_running() and _worker["key"] == key):
        try:
            load(key)
        except ValueError as e:
            raise LLMError(str(e)) from None
    deadline = time.time() + LOAD_TIMEOUT
    while time.time() < deadline:
        st = await asyncio.to_thread(worker_status)
        if st["state"] == "ready":
            return
        if st["state"] in ("error", "stopped"):
            raise LLMError(f"로컬 LLM 을 올리지 못했어요: {st.get('error') or st['state']}")
        await asyncio.sleep(0.5)
    raise LLMError("로컬 LLM 을 올리는 데 너무 오래 걸려요")


async def chat(messages: list[dict], *, tools: list[dict] | None = None, max_tokens: int = 4096,
               temperature: float | None = None) -> dict:
    """작업 프로세스에 대화 한 턴을 보낸다. 취소되면(에이전트 세션 중지 등) 작업 프로세스의 생성도 멈춘다."""
    await ensure_ready()
    rid = secrets.token_hex(8)
    body: dict = {"id": rid, "messages": messages, "tools": tools, "max_tokens": max_tokens,
                  "thinking": options()["thinking"]}
    if temperature is not None:
        body["temperature"] = temperature
    try:
        return await asyncio.to_thread(_request, "/chat", body, CHAT_TIMEOUT)
    except asyncio.CancelledError:
        try:
            _request("/cancel", {"id": rid}, timeout=1.0)
        except (LLMError, OSError):
            pass
        raise
    except OSError as e:
        raise LLMError(f"로컬 LLM 연결 실패: {e}") from None


# ---------- 화면용 ----------

_device: dict = {}


def device_info() -> dict:
    """Metal 장치 정보 (권장 작업 집합 등). 이 서버에서 Metal 을 초기화하지 않도록 한 번만 따로 실행해 기억한다."""
    if not _device:
        try:
            out = subprocess.run([sys.executable, "-c", "import json, mlx.core as mx; print(json.dumps(mx.device_info()))"],
                                 capture_output=True, text=True, timeout=30)
            _device.update(json.loads(out.stdout.strip().splitlines()[-1]))
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            _device["error"] = "mlx 를 불러오지 못했어요"
    return _device


def memory_info() -> dict:
    """맥 메모리와 GPU 한도 (sysctl iogpu.wired_limit_mb 를 올렸는지)."""
    total = sysctl_int("hw.memsize")
    wired_mb = sysctl_int("iogpu.wired_limit_mb")
    limit, source = gpu_limit(device_info().get("max_recommended_working_set_size"))
    target = int(total * 0.75) if total else None
    return {"memory_bytes": total, "wired_limit_mb": wired_mb, "gpu_limit_bytes": limit, "gpu_limit_source": source,
            "target_bytes": target, "margin_bytes": int(MARGIN_GB * GIB),
            "apple_silicon": sys.platform == "darwin" and os.uname().machine == "arm64"}


def status() -> dict:
    opts = options()
    return {"provider": provider(), "options": opts, "models": [model_status(k) for k in CATALOG],
            "worker": worker_status(), "memory": memory_info()}
