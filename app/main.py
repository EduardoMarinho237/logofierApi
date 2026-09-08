from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import inspect, text

from app.config import settings
from app.database import engine
from app.limiter import limiter
from app.routes import admin, auth, jobs, logos, presets, preview, users


def _ensure_job_columns() -> None:
    """Backward-compat safety net for columns introduced before Alembic enforcement."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        inspector = inspect(conn)
        if "jobs" not in inspector.get_table_names():
            return
        cols = {c["name"] for c in inspector.get_columns("jobs")}
        if "mode" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN mode VARCHAR(32) NOT NULL DEFAULT 'multiple_pdfs'"))
        if "logo_keys" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN logo_keys JSON"))
        if "logo_names" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN logo_names JSON"))
        if "source_names" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN source_names JSON"))


def _ensure_preset_columns() -> None:
    """Backward-compat safety net for columns introduced after Alembic enforcement."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        inspector = inspect(conn)
        if "presets" not in inspector.get_table_names():
            return
        cols = {c["name"] for c in inspector.get_columns("presets")}
        if "mode" not in cols:
            conn.execute(text("ALTER TABLE presets ADD COLUMN mode VARCHAR(32) NOT NULL DEFAULT 'multiple_pdfs'"))
        if "pos_strategy" not in cols:
            conn.execute(text("ALTER TABLE presets ADD COLUMN pos_strategy VARCHAR(32) NOT NULL DEFAULT 'shared'"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    _ensure_job_columns()
    _ensure_preset_columns()
    yield


app = FastAPI(title="Logofier API", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(logos.router, prefix="/api/logos", tags=["logos"])
app.include_router(presets.router, prefix="/api/presets", tags=["presets"])
app.include_router(preview.router, prefix="/api/preview", tags=["preview"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])


@app.get("/health")
def health_check():
    return {"status": "ok"}
