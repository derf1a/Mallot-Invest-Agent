"""구조 규칙 — 고치다가 계층이 엉키지 않게 테스트로 잠근다.

- 공용 계층 app/core · app/llm · app/runs 는 기능(invest · system)을 모듈 맨 위에서 불러오지 않는다.
- 투자 기능의 바닥 계층(broker · marketdata · analysis)은 에이전트 계층(debate · ops · advice)을 맨 위에서 불러오지 않는다.
  에이전트 계층끼리는 서로 부른다 (토론 · 운용 · 비서가 한 덩어리로 움직인다).
- 라우터 모듈(api.py · api_*.py)은 다른 모듈이 불러오지 않는다. 같이 쓸 함수는 라우터 밖에 둔다.
- 라우터는 모두 /api 아래에 있고, registry 가 빠짐없이 등록한다.
함수 안에서 늦게 부르는 import 는 허용한다 (순환을 끊는 용도).
"""

import ast
from pathlib import Path

from app import registry

APP = Path(__file__).resolve().parents[1] / "app"
SHARED = {"core": ("app.core",), "llm": ("app.core", "app.llm"), "runs": ("app.core", "app.llm", "app.runs")}
FOUNDATION = ("broker", "marketdata", "analysis")
AGENT_LAYERS = tuple(f"app.invest.{p}" for p in ("debate", "ops", "advice"))


def _module(path: Path) -> str:
    return ".".join(("app", *path.relative_to(APP).with_suffix("").parts)).removesuffix(".__init__")


def _top_imports(path: Path) -> set[str]:
    """모듈 맨 위(함수 밖)에서 불러오는 app.* 이름. `from app.x import y` 는 y 가 하위 모듈일 수 있어 둘 다 넣는다."""
    out: set[str] = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.startswith("app"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


MODULES = {_module(p): _top_imports(p) for p in APP.rglob("*.py")}


def _is_api(name: str) -> bool:
    leaf = name.rsplit(".", 1)[-1]
    return name.count(".") == 2 and (leaf == "api" or leaf.startswith("api_"))


def test_shared_layers_do_not_import_features():
    for name, imports in MODULES.items():
        parts = name.split(".")
        if len(parts) < 2 or parts[1] not in SHARED:
            continue
        bad = sorted(i for i in imports if i != "app" and not i.startswith(SHARED[parts[1]]))
        assert not bad, f"{name} 은 공용 계층인데 기능 모듈을 불러온다: {bad}"


def test_invest_foundation_does_not_import_agent_layers():
    for name, imports in MODULES.items():
        parts = name.split(".")
        if len(parts) < 3 or parts[1] != "invest" or parts[2] not in FOUNDATION:
            continue
        bad = sorted(i for i in imports if i.startswith(AGENT_LAYERS))
        assert not bad, f"{name} 은 바닥 계층인데 에이전트 계층을 불러온다: {bad}"


def test_routers_are_not_imported_by_other_modules():
    for name, imports in MODULES.items():
        bad = sorted(i for i in imports if _is_api(i) and i in MODULES)
        assert not bad, f"{name} 이 라우터 모듈을 불러온다 (공용 함수는 라우터 밖으로): {bad}"


def test_every_router_is_registered_under_api():
    routers = registry.routers()
    api_modules = [n for n in MODULES if _is_api(n)]
    assert len(routers) == len(api_modules), "router 가 없는 api 모듈이 있다"
    for r in routers:
        assert r.prefix.startswith("/api"), r.prefix
