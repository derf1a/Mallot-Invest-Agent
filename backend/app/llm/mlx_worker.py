"""로컬 LLM 작업 프로세스 (mlx-lm, Apple Silicon). 백엔드(app/llm/local.py)가 띄우고 127.0.0.1 HTTP 로만 부른다.

    python -m app.llm.mlx_worker serve --model <폴더> --port <포트>     (인증 토큰: 환경변수 MIA_LOCAL_TOKEN)
    python -m app.llm.mlx_worker download --repo <HF 저장소> --dir <폴더>

- 메모리: 모델을 올린 뒤 GPU 한도(sysctl iogpu.wired_limit_mb, 0 이면 Metal 권장 작업 집합)에서 가중치·고정 상태
  (선형 어텐션 층의 상태)·여유분을 빼고 남는 만큼을 KV 캐시 토큰 한도로 잡는다. 입력이 한도를 넘는 요청은 받지 않고,
  출력 길이는 한도 안으로 줄인다 — 메모리 부족으로 맥 전체가 스왑에 빠지지 않게.
- 생성은 한 번에 하나(잠금). 도구 루프처럼 같은 대화가 이어지면 앞 토큰의 KV 캐시를 다시 쓴다(앞부분이 정확히 같을 때만).
- 부모(백엔드) 프로세스가 사라지면 스스로 끝난다 — 개발 서버가 다시 뜰 때 모델이 메모리에 남지 않게.

mlx 는 serve 안에서만 import 한다 (백엔드·테스트가 이 모듈의 계산 함수만 가져다 쓸 수 있게).
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GIB = 1024**3
MIN_KV_TOKENS = 2048
TOKEN_HEADER = "X-Worker-Token"
THINKING = ("off", "low", "medium", "xhigh")  # 끔 + 템플릿의 reasoning_effort


def sysctl_int(name: str) -> int | None:
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=3)
        return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def gpu_limit(recommended: int | None) -> tuple[int | None, str]:
    """GPU 가 쓸 수 있는 메모리 한도와 출처. iogpu.wired_limit_mb 를 올려 두었으면 그 값, 아니면 Metal 권장값."""
    mb = sysctl_int("iogpu.wired_limit_mb")
    if mb:
        return mb * 1024 * 1024, "iogpu.wired_limit_mb"
    return recommended, "metal_recommended"


def kv_plan(limit: int, weights: int, fixed_state: int, per_token: int, max_context: int, margin: int) -> dict:
    """한도 - 가중치 - 고정 상태 - 여유분 = KV 캐시 예산 → 토큰 수 (모델 최대 길이를 넘지 않게)."""
    budget = max(0, limit - weights - fixed_state - margin)
    tokens = min(budget // per_token if per_token else 0, max_context)
    return {"limit_bytes": limit, "weights_bytes": weights, "fixed_state_bytes": fixed_state, "margin_bytes": margin,
            "kv_bytes_per_token": per_token, "kv_tokens": int(tokens), "kv_budget_bytes": int(tokens * per_token)}


def looping(tokens: list[int], window: int = 96, max_period: int = 32) -> bool:
    """마지막 window 토큰이 max_period 이하 길이의 같은 조각을 되풀이하는지 — 낮은 비트 모델이 빠지는 반복 루프.
    루프에 빠지면 출력 한도까지 몇 분씩 헛돌기 때문에 생성을 멈춘다."""
    if len(tokens) < window:
        return False
    tail = tokens[-window:]
    return any(all(tail[i] == tail[i - p] for i in range(p, window)) for p in range(1, max_period + 1))


def split_reasoning(text: str, thinking: bool) -> tuple[str, str]:
    """생각 모드면 '<think>' 는 프롬프트에 있고 출력은 '생각 … </think> 답' 이다. (생각, 답)."""
    if thinking and "</think>" in text:
        reasoning, _, content = text.partition("</think>")
        return reasoning.strip(), content.strip()
    if thinking:  # 생각만 하다 길이 제한에 걸림
        return text.strip(), ""
    return "", text.strip()


def tool_blocks(content: str, start: str = "<tool_call>", end: str = "</tool_call>") -> tuple[str, list[str]]:
    """답에서 도구 호출 블록을 떼어 낸다. (도구 호출 앞의 글, 블록 안쪽 글 목록). 닫히지 않은 마지막 블록도 받는다."""
    head, _, rest = content.partition(start)
    if not rest:
        return content.strip(), []
    blocks = []
    for part in (start + rest).split(start)[1:]:
        inner, _, _ = part.partition(end)
        if inner.strip():
            blocks.append(inner.strip())
    return head.strip(), blocks


class Cancelled(Exception):
    pass


class Engine:
    """모델 하나와 마지막 대화의 KV 캐시."""

    def __init__(self, model_dir: str, margin_gb: float):
        self.model_dir = model_dir
        self.margin = int(margin_gb * GIB)
        self.state = "loading"
        self.error: str | None = None
        self.plan: dict = {}
        self.info: dict = {"model_dir": model_dir, "pid": os.getpid()}
        self.lock = threading.Lock()
        self.cancelled: set[str] = set()
        self.current: str | None = None
        self.waiting = 0
        self.served = 0
        self.cache = None
        self.cache_tokens: list[int] = []

    # ---- 올리기 ----
    def load(self) -> None:
        try:
            self._load()
            self.state = "ready"
        except Exception as e:  # noqa: BLE001 — 화면에 보여 줄 오류로 남긴다
            self.state, self.error = "error", f"{type(e).__name__}: {e}"
            print(f"[mlx_worker] load failed: {self.error}", file=sys.stderr, flush=True)

    def _load(self) -> None:
        import mlx.core as mx
        from mlx.utils import tree_flatten
        from mlx_lm import load
        from mlx_lm.generate import generate_step
        from mlx_lm.models.cache import make_prompt_cache

        started = time.perf_counter()
        device = mx.device_info()
        limit, source = gpu_limit(device.get("max_recommended_working_set_size"))
        if limit:
            mx.set_wired_limit(min(limit, device.get("max_recommended_working_set_size") or limit))
        mx.set_cache_limit(GIB // 2)  # 버퍼 캐시가 KV 예산을 잠식하지 않게
        self.model, self.tokenizer = load(self.model_dir)
        weights = sum(v.nbytes for _, v in tree_flatten(self.model.parameters()))
        # 짧게 한 번 돌려 커널을 준비하고, 캐시 모양에서 토큰당 KV 크기와 고정 상태 크기를 잰다
        cache = make_prompt_cache(self.model)
        warm = self.tokenizer.apply_chat_template([{"role": "user", "content": "안녕"}], add_generation_prompt=True,
                                                  enable_thinking=False)
        for _ in generate_step(mx.array(warm), self.model, max_tokens=1, prompt_cache=cache):
            pass
        per_token = fixed = 0
        for c in cache:
            keys = getattr(c, "keys", None)
            if keys is not None and getattr(c, "values", None) is not None:
                per_token += (keys.nbytes + c.values.nbytes) // keys.shape[2]
            else:
                fixed += sum(a.nbytes for a in getattr(c, "cache", None) or [] if a is not None)
        del cache
        mx.clear_cache()
        with open(os.path.join(self.model_dir, "config.json"), encoding="utf-8") as f:
            config = json.load(f)
        text = config.get("text_config", config)
        max_context = int(text.get("max_position_embeddings") or 32768)
        if not limit:
            raise RuntimeError("GPU 메모리 한도를 알 수 없어요")
        self.plan = {**kv_plan(limit, weights, fixed, per_token, max_context, self.margin), "limit_source": source,
                     "max_context": max_context}
        if self.plan["kv_tokens"] < MIN_KV_TOKENS:
            raise RuntimeError(f"KV 캐시에 쓸 메모리가 부족해요 (토큰 {self.plan['kv_tokens']}개) — GPU 메모리 한도를 올리세요")
        self.info.update(load_seconds=round(time.perf_counter() - started, 1), memory_size=device.get("memory_size"),
                         recommended_bytes=device.get("max_recommended_working_set_size"),
                         active_bytes=mx.get_active_memory(), tool_calling=self.tokenizer.has_tool_calling,
                         model_type=config.get("model_type"), quantization=config.get("quantization"))
        print(f"[mlx_worker] ready {json.dumps(self.plan)}", file=sys.stderr, flush=True)

    # ---- 생성 ----
    def health(self) -> dict:
        out = {"state": self.state, "error": self.error, "plan": self.plan, **self.info,
               "busy": self.current is not None, "waiting": self.waiting, "served": self.served}
        if self.state == "ready":
            import mlx.core as mx
            out.update(active_bytes=mx.get_active_memory(), peak_bytes=mx.get_peak_memory(),
                       cached_tokens=len(self.cache_tokens))
        return out

    def chat(self, req: dict) -> dict:
        if self.state != "ready":
            raise RuntimeError(self.error or "모델을 올리는 중이에요")
        rid = str(req.get("id") or "")
        self.waiting += 1
        with self.lock:
            self.waiting -= 1
            if rid in self.cancelled:
                self.cancelled.discard(rid)
                raise Cancelled()
            self.current = rid
            try:
                return self._chat(req, rid)
            finally:
                self.current = None
                self.cancelled.discard(rid)

    def _chat(self, req: dict, rid: str) -> dict:
        import mlx.core as mx
        from mlx_lm import stream_generate
        from mlx_lm.models.cache import make_prompt_cache
        from mlx_lm.sample_utils import make_logits_processors, make_sampler

        thinking = req.get("thinking") if req.get("thinking") in THINKING else "off"
        tools = req.get("tools") or None
        kwargs: dict = {"enable_thinking": thinking != "off"}
        if thinking != "off":
            kwargs["reasoning_effort"] = thinking
        if tools:
            kwargs["tools"] = tools
        tokens = list(self.tokenizer.apply_chat_template(req["messages"], add_generation_prompt=True, **kwargs))
        room = self.plan["kv_tokens"] - len(tokens)
        if room < 64:
            raise ValueError(f"입력이 너무 길어요: {len(tokens):,}토큰 — KV 캐시 한도 {self.plan['kv_tokens']:,}토큰")
        max_tokens = max(1, min(int(req.get("max_tokens") or 4096), room))

        reuse = (self.cache is not None and 0 < len(self.cache_tokens) < len(tokens)
                 and tokens[:len(self.cache_tokens)] == self.cache_tokens)
        cache, cached = (self.cache, len(self.cache_tokens)) if reuse else (make_prompt_cache(self.model), 0)
        self.cache, self.cache_tokens = None, []  # 중간에 실패하면 버린다
        if not reuse:
            mx.clear_cache()

        def progress(done: int, total: int) -> None:
            if rid in self.cancelled:
                raise Cancelled()

        hot = thinking != "off"
        sampler = make_sampler(temp=float(req.get("temperature", 1.0 if hot else 0.7)),
                               top_p=float(req.get("top_p", 0.95 if hot else 0.8)), top_k=20)
        penalty = float(req.get("presence_penalty") or 0)
        processors = make_logits_processors(presence_penalty=penalty, presence_context_size=256) if penalty else None
        started = time.perf_counter()
        first_at = None
        text, out_tokens, last, finish = "", [], None, "stop"
        for r in stream_generate(self.model, self.tokenizer, mx.array(tokens[cached:]), max_tokens=max_tokens,
                                 sampler=sampler, logits_processors=processors, prompt_cache=cache,
                                 prompt_progress_callback=progress):
            if first_at is None:
                first_at = time.perf_counter()
            if rid in self.cancelled:
                raise Cancelled()
            text += r.text
            out_tokens.append(r.token)
            last, finish = r, r.finish_reason or finish
            if len(out_tokens) % 16 == 0 and looping(out_tokens):
                finish = "repetition"
                break
        ended = time.perf_counter()
        self.cache, self.cache_tokens = cache, tokens + out_tokens
        self.served += 1

        reasoning, content = split_reasoning(text, hot)
        head, blocks = tool_blocks(content) if self.tokenizer.has_tool_calling else (content, [])
        calls, errors = [], []
        for b in blocks:
            try:
                calls.append(self.tokenizer.tool_parser(b, tools))
            except Exception as e:  # noqa: BLE001 — 형식이 틀린 호출은 모델에게 오류로 돌려준다
                errors.append(f"{type(e).__name__}: {e} · {b[:200]}")
        first_at = first_at or ended
        return {
            "content": content, "text": head, "reasoning": reasoning, "tool_calls": calls, "tool_call_errors": errors,
            "finish_reason": finish,
            "usage": {"prompt_tokens": len(tokens), "cached_tokens": cached, "output_tokens": len(out_tokens)},
            "timing": {"prefill_ms": int((first_at - started) * 1000), "generate_ms": int((ended - first_at) * 1000),
                       "prompt_tps": round(last.prompt_tps, 1) if last else None,
                       "generation_tps": round(last.generation_tps, 1) if last else None},
            "peak_bytes": int(mx.get_peak_memory()), "kv_tokens": self.plan["kv_tokens"], "max_tokens": max_tokens,
        }

    def cancel(self, rid: str) -> None:
        if rid:
            self.cancelled.add(rid)


def serve(model_dir: str, port: int, margin_gb: float) -> None:
    token = os.environ.get("MIA_LOCAL_TOKEN", "")
    engine = Engine(model_dir, margin_gb)
    parent = os.getppid()

    def watch_parent() -> None:
        while True:
            if os.getppid() != parent:
                os._exit(0)
            time.sleep(2)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # 요청마다 로그를 남기지 않는다
            pass

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if token and self.headers.get(TOKEN_HEADER) != token:
                self._send(403, {"error": "forbidden"})
                return False
            return True

        def do_GET(self) -> None:
            if not self._authorized():
                return
            if self.path == "/health":
                self._send(200, engine.health())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "JSON 이 아닙니다"})
                return
            if self.path == "/cancel":
                engine.cancel(str(body.get("id") or ""))
                self._send(200, {"ok": True})
            elif self.path == "/chat":
                try:
                    self._send(200, engine.chat(body))
                except Cancelled:
                    self._send(409, {"error": "취소됨", "code": "cancelled"})
                except ValueError as e:
                    self._send(413, {"error": str(e), "code": "too_long"})
                except Exception as e:  # noqa: BLE001
                    self._send(500, {"error": f"{type(e).__name__}: {e}"})
            else:
                self._send(404, {"error": "not found"})

    threading.Thread(target=watch_parent, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=engine.load, daemon=True).start()
    print(f"[mlx_worker] listening 127.0.0.1:{port} model={model_dir}", file=sys.stderr, flush=True)
    server.serve_forever()


def download(repo: str, target: str) -> None:
    """HF 저장소를 폴더로 내려받는다(이어받기 가능). 시작할 때 전체 크기를 .download.json 에 남겨 진행률 계산에 쓴다."""
    from huggingface_hub import HfApi, snapshot_download

    info = HfApi().model_info(repo, files_metadata=True)
    files = {s.rfilename: s.size for s in info.siblings or [] if s.size is not None}
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, ".download.json"), "w", encoding="utf-8") as f:
        json.dump({"repo": repo, "revision": info.sha, "total_bytes": sum(files.values()), "files": files}, f)
    snapshot_download(repo, local_dir=target, revision=info.sha)
    print("[mlx_worker] download done", file=sys.stderr, flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--model", required=True)
    s.add_argument("--port", type=int, required=True)
    s.add_argument("--margin-gb", type=float, default=1.0, help="KV 캐시 예산에서 빼 둘 여유분 (GiB)")
    d = sub.add_parser("download")
    d.add_argument("--repo", required=True)
    d.add_argument("--dir", required=True)
    args = p.parse_args()
    if args.cmd == "serve":
        serve(args.model, args.port, args.margin_gb)
    else:
        download(args.repo, args.dir)


if __name__ == "__main__":
    main()
