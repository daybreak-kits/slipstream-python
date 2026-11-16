from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from l07_bootstrap.app_factory import _mount_web_dist

SPA_ROUTE = "/{full_path:path}"
# The sandbox refuses to create the pytest tmp tree under %TEMP%, so fixtures
# build throwaway directories inside the repository (same reason as the
# database fixture in conftest.py).
DIST_ROOT = Path(__file__).resolve().parents[1] / ".pytest-dist"


def _build_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return dist


def _spa_endpoint(app: FastAPI):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == SPA_ROUTE)


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.fixture
def case_root() -> Iterator[Path]:
    root = DIST_ROOT / f"case-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def dist(case_root: Path) -> Path:
    return _build_dist(case_root)


async def test_index_is_served_at_root(dist: Path) -> None:
    app = FastAPI()
    _mount_web_dist(app, dist)

    response = await _get(app, "/")

    assert response.status_code == 200
    assert "spa" in response.text


async def test_unknown_route_falls_back_to_index_for_browser_routing(dist: Path) -> None:
    app = FastAPI()
    _mount_web_dist(app, dist)

    response = await _get(app, "/channels")

    assert response.status_code == 200
    assert "spa" in response.text


async def test_built_assets_are_served_and_win_over_spa_fallback(dist: Path) -> None:
    app = FastAPI()
    _mount_web_dist(app, dist)

    response = await _get(app, "/assets/app.js")

    assert response.status_code == 200
    assert response.text == "console.log(1)"


async def test_api_routes_registered_before_mount_keep_priority(dist: Path) -> None:
    app = FastAPI()

    @app.get("/api/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    _mount_web_dist(app, dist)

    response = await _get(app, "/api/ping")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_path_traversal_outside_dist_falls_back_to_index(dist: Path) -> None:
    app = FastAPI()
    _mount_web_dist(app, dist)
    secret = dist.parent / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")

    response = await _spa_endpoint(app)("../../secret.txt")

    assert Path(response.path) == dist / "index.html"


async def test_no_mount_without_a_build(case_root: Path) -> None:
    app = FastAPI()
    _mount_web_dist(app, case_root / "missing")

    assert not [route for route in app.routes if getattr(route, "path", None) == SPA_ROUTE]
