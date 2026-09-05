import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.db import Database
from app.models import Base
from app.seeds import ensure_seeded

log = logging.getLogger("healthhub")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings)

    # MCP 写通道（fastmcp Streamable HTTP）。失败不阻塞主服务。
    mcp_http = None
    try:
        from app.mcp import create_mcp
        mcp_http = create_mcp(db, settings).http_app(transport="streamable-http", path="/")
    except Exception:
        log.warning("MCP channel unavailable", exc_info=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = app.state.db
        Base.metadata.create_all(db.engine)
        from app.db import migrate
        migrate(db.engine)
        with db.SessionLocal() as session:
            ensure_seeded(session, settings.seed_dir)
        scheduler = None
        if settings.scheduler_enabled:
            from app.scheduler import make_scheduler
            scheduler = make_scheduler(settings)
            scheduler.start()
            log.info("scheduler started, daily report at %s %s", settings.daily_report_time, settings.app_tz)
        if mcp_http is not None:
            async with mcp_http.lifespan(app):
                yield
        else:
            yield
        if scheduler:
            scheduler.shutdown(wait=False)
        db.engine.dispose()

    app = FastAPI(title="HealthHub", version="0.2.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.db = db

    if mcp_http is not None:
        from app.mcp import bearer_gate
        app.mount("/mcp", bearer_gate(mcp_http, settings), name="mcp")
        log.info("MCP write channel mounted at /mcp")

    from app.routers import admin, conditions, dashboard, discussion, entry, events, exports, ingest, insights, mappings, observations, persons, records, sources_page, onepager, speak, uploads
    app.include_router(persons.router)
    app.include_router(conditions.router)
    app.include_router(ingest.router)
    app.include_router(observations.router)
    app.include_router(insights.router)
    app.include_router(mappings.router)
    app.include_router(admin.router)
    app.include_router(dashboard.router)
    app.include_router(records.router)
    app.include_router(entry.router)
    app.include_router(events.router)
    app.include_router(discussion.router)
    app.include_router(uploads.router)
    app.include_router(sources_page.router)
    app.include_router(onepager.router)
    app.include_router(exports.router)
    app.include_router(speak.router)

    from fastapi.staticfiles import StaticFiles as _SM
    from pathlib import Path as _P
    _static = _P(__file__).parent / "static"
    if _static.exists():
        app.mount("/static", _SM(directory=str(_static)), name="static")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return {"name": "HealthHub 健康面板", "short_name": "HealthHub",
                "start_url": "/dashboard/2", "display": "standalone",
                "background_color": "#f2f5f8", "theme_color": "#0f6aad",
                "icons": [{"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"}]}

    @app.get("/icon-180.png", include_in_schema=False)
    def icon():
        from fastapi.responses import FileResponse
        from pathlib import Path
        return FileResponse(Path(__file__).parent / "icon-180.png", media_type="image/png")

    @app.get("/healthz", tags=["admin"])
    def healthz():
        from sqlalchemy import text

        db: Database = app.state.db
        ok = True
        try:
            with db.SessionLocal() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            ok = False
        return {"status": "ok" if ok else "degraded", "db": ok,
                "llm_configured": bool(settings.llm_api_key),
                "scheduler": settings.scheduler_enabled}

    return app


app = create_app()
