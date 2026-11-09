"""기능 패키지 자동 등록 — 새 기능은 app/<기능>/ 폴더를 만들면 main 을 고치지 않아도 붙는다.

- 라우터: 기능 패키지 안의 api.py · api_*.py 모듈에 `router` 가 있으면 등록한다.
- 작업(run): 기능 패키지 안의 jobs.py 모듈 또는 jobs/ 패키지의 모듈을 불러온다 (import 될 때 runner.register(...)).
- 백그라운드 서비스: 기능 패키지 안의 service.py 모듈의 start()·stop() 을 서버 시작·종료 때 부른다 (예: 자동 운용 일정).
"""

import importlib
import pkgutil

from fastapi import APIRouter

import app


def feature_packages() -> list[str]:
    return sorted(m.name for m in pkgutil.iter_modules(app.__path__) if m.ispkg)


def _modules(package: str) -> list[pkgutil.ModuleInfo]:
    return sorted(pkgutil.iter_modules(importlib.import_module(package).__path__), key=lambda m: m.name)


def routers() -> list[APIRouter]:
    out = []
    for pkg in feature_packages():
        for mod in _modules(f"app.{pkg}"):
            if mod.name == "api" or mod.name.startswith("api_"):
                module = importlib.import_module(f"app.{pkg}.{mod.name}")
                if hasattr(module, "router"):
                    out.append(module.router)
    return out


def services() -> list:
    return [importlib.import_module(f"app.{pkg}.service") for pkg in feature_packages()
            if any(m.name == "service" for m in _modules(f"app.{pkg}"))]


def load_jobs() -> None:
    for pkg in feature_packages():
        jobs = next((m for m in _modules(f"app.{pkg}") if m.name == "jobs"), None)
        if jobs is None:
            continue
        importlib.import_module(f"app.{pkg}.jobs")
        if jobs.ispkg:
            for mod in _modules(f"app.{pkg}.jobs"):
                importlib.import_module(f"app.{pkg}.jobs.{mod.name}")
