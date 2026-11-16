from __future__ import annotations

import asyncio
import signal
import threading
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from l01_core import runtime
from l01_core.config import Settings, get_settings
from l01_core.ids import new_request_id
from l02_infra.db import build_engine, build_sessionmaker, init_db
from l05_service import body_logging, catalog, ledger, log_pipeline, retention, route_settings, route_stats, upstream_settings, usage_sink
from l05_service.auth import ensure_default_admin
from l05_service.config_catalog import Catalog, bind_catalog
from l05_service.model_defaults import ModelDefaultsService
from l05_service.llm import gateway
from l05_service.stream_registry import StreamRegistry
from l06_api.errors import install_exception_handlers
from l06_api.routers import (
    auth,
    catalog,
    channel_models,
    channels,
    combos,
    dashboard,
    groups,
    health,
    keys,
    logs,
    model_defaults,
    settings as settings_router,
    v1,
)


_WEB_DIST_DIR = Path(__file__).resolve().parents[2].parent / "web" / "dist"


def _mount_web_dist(app: FastAPI, dist_dir: Path | None = None) -> None:
    """Serve the built web SPA from the API origin when a build exists.

    Single-origin production avoids the dev-only vite proxy: the browser
    talks to one port and settings.ts already derives the API base from
    window.location.origin. API and docs routes are registered before this
    catch-all, so /api, /v1 and /docs keep priority.
    """
    dist = dist_dir or _WEB_DIST_DIR
    index_file = dist / "index.html"
    if not index_file.is_file():
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="web_assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        candidate = (dist / full_path).resolve() if full_path else dist
        if (
            full_path
            and candidate.is_relative_to(dist.resolve())
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        return FileResponse(index_file)


def create_app(settings: Settings | None = None, database_url: str | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    # Hot-path helpers (log pipeline, ledger) read the instance the app runs
    # with, not a second copy built from the environment.
    runtime.bind_settings(app_settings)
    # Fail fast on an invalid USAGE_PIPELINE before any traffic is served.
    _ = app_settings.zero_sql_pipeline
    db_engine = build_engine(
        database_url or app_settings.database_url, settings_obj=app_settings
    )

    session_factory = build_sessionmaker(db_engine)
    stream_registry = StreamRegistry()
    log_pipeline.observability.bind(session_factory)
    catalog_cache = Catalog(session_factory, app_settings)
    model_defaults_service = ModelDefaultsService(session_factory, app_settings)

    def _on_model_defaults_refreshed() -> None:
        # A fresh snapshot can change the directory listing: drop both caches
        # so the next read shows it (ADR-073 decision 2 / D3).
        catalog.reset_model_directory_cache()
        catalog_cache.mark_dirty()

    model_defaults_service.on_refreshed = _on_model_defaults_refreshed
    if app_settings.zero_sql_pipeline:
        bind_catalog(session_factory, catalog_cache, engine=db_engine)
        body_logging.bind_body_catalog(session_factory, catalog_cache)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await gateway.startup(app_settings)
        await init_db(db_engine)
        await ensure_default_admin(session_factory, app_settings)
        # Warm the upstream-timeouts cache so request paths never pay the
        # app_settings read (zero-SQL invariant); refreshed by save/reset and
        # a lazy TTL re-read.
        async with session_factory() as session:
            await upstream_settings.get_upstream_timeouts(session)
            # Warm the ADR-077 routing caches too: the request path resolves
            # strategy ordering from memory only (zero-SQL invariant).
            await route_settings.get_route_settings(session)
            await route_stats.refresh_if_stale(session)
        # The read cache loads before traffic is admitted; a failed load is a
        # startup failure, not a degraded-but-running app.
        if app_settings.zero_sql_pipeline:
            await catalog_cache.startup()
        # Replay any WAL left by a previous process before serving traffic.
        await ledger.ledger_service.startup(session_factory)
        await usage_sink.usage_sink.startup(session_factory)
        retention_task = asyncio.create_task(
            retention.retention_loop(
                session_factory, timezone=app_settings.default_timezone
            )
        )
        restored_signals: list[tuple[int, object]] = []
        if (
            app_settings.app_env in {"production", "staging"}
            and threading.current_thread() is threading.main_thread()
        ):
            for signum in (signal.SIGTERM, signal.SIGINT):
                try:
                    previous = signal.getsignal(signum)

                    def _draining_handler(
                        received: int, frame, previous=previous
                    ) -> None:  # type: ignore[no-untyped-def]
                        stream_registry.begin_shutdown()
                        if callable(previous):
                            previous(received, frame)

                    signal.signal(signum, _draining_handler)
                    restored_signals.append((signum, previous))
                except (OSError, ValueError, RuntimeError):
                    continue
        try:
            yield
        finally:
            # Stop admitting new SSE relays, then let in-flight streams finish
            # before the shared HTTP and DB pools are torn down.
            stream_registry.begin_shutdown()
            await stream_registry.drain(float(app_settings.shutdown_grace_s))
            retention_task.cancel()
            with suppress(asyncio.CancelledError):
                await retention_task
            await ledger.ledger_service.drain()
            await usage_sink.usage_sink.drain()
            await log_pipeline.drain_observability()
            await body_logging.drain_body_writes()
            if app_settings.zero_sql_pipeline:
                await catalog_cache.shutdown()
            await gateway.shutdown()
            await db_engine.dispose()
            for signum, previous in restored_signals:
                try:
                    signal.signal(signum, previous)
                except (OSError, ValueError, RuntimeError):
                    continue

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.db_engine = db_engine
    app.state.session_factory = session_factory
    app.state.stream_registry = stream_registry
    app.state.catalog = catalog_cache
    app.state.model_defaults = model_defaults_service

    @app.middleware("http")
    async def attach_request_id(request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = request.headers.get("x-request-id") or new_request_id()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_exception_handlers(app)
    # The log page and the dashboard must see their own writes: the diagnostics
    # columns are patched by a background task, so a read drains it first (a
    # no-op when nothing is queued).
    _flush_log_writes = [
        Depends(log_pipeline.flush_observability),
        Depends(usage_sink.flush_usage_records),
    ]
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(channels.router)
    app.include_router(channel_models.router)
    app.include_router(combos.router)
    app.include_router(dashboard.router, dependencies=_flush_log_writes)
    app.include_router(groups.router)
    app.include_router(keys.router)
    app.include_router(catalog.router)
    app.include_router(model_defaults.router)
    app.include_router(logs.router, dependencies=_flush_log_writes)
    app.include_router(settings_router.router)
    app.include_router(v1.router)
    # Single-origin production: serve the built SPA when it exists (dev keeps
    # using the vite proxy). Registered last so API routes win.
    _mount_web_dist(app)
    return app
