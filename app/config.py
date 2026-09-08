from pydantic import field_validator
from pydantic_settings import BaseSettings

RESERVED_JWT_SECRETS = {
    "change-me-in-production",
    "dev-secret-change-me",
    "test-secret",
}


class Settings(BaseSettings):
    # --- Database / infra -------------------------------------------------
    DATABASE_URL: str = "sqlite:///./logofier.db"

    # --- Orchestration (docker compose) ------------------------------------
    # Set by docker compose via .env to build DATABASE_URL and the Postgres
    # container. Declared here so the app tolerates them in the shared .env.
    POSTGRES_USER: str = "logofier"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "logofier"

    # --- Auth -------------------------------------------------------------
    # Required. No default on purpose: starting with a weak/placeholder
    # secret would allow token forgery.
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRATION_DAYS: int = 14
    PASSWORD_MIN_LENGTH: int = 8

    LOGIN_MAX_FAILED_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 30
    RATE_LIMIT_LOGIN: str = "5/minute"

    # --- Cloudflare R2 storage --------------------------------------------
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "logofier-files"
    R2_PUBLIC_URL: str = ""
    LOCAL_STORAGE_DIR: str = ""

    # --- CORS -------------------------------------------------------------
    # Kept as a plain string on purpose: pydantic-settings force-decodes
    # complex types (list[str]) as JSON before validators run, so a
    # comma-separated value would crash startup. Parse via cors_origins_list.
    CORS_ORIGINS: str = "http://localhost:3000,https://logofier.vercel.app"

    @property
    def cors_origins_list(self) -> list[str]:
        s = self.CORS_ORIGINS.strip()
        if not s:
            return []
        try:
            import json as _json

            parsed = _json.loads(s)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
        return [part.strip() for part in s.split(",") if part.strip()]

    # --- Upload limits (centralized — tune here) --------------------------
    MAX_AVATAR_BYTES: int = 5 * 1024 * 1024
    MAX_LOGO_BYTES: int = 5 * 1024 * 1024
    MAX_PDF_BYTES: int = 50 * 1024 * 1024
    MAX_PDFS_PER_REQUEST: int = 20
    MAX_PDFS_PER_JOB: int = 100
    MAX_UPLOADED_LOGOS: int = 50
    MAX_LOGO_IDS: int = 100
    MAX_LOGOS_PER_JOB: int = 100

    # --- Processing limits (PDFs / images) --------------------------------
    MAX_PDF_PAGES: int = 500
    MAX_PAGE_SIZE_POINTS: int = 10_000
    MAX_RENDER_DPI: int = 96
    MAX_REVIEW_PAGES: int = 20
    MAX_IMAGE_PIXELS: int = 50_000_000
    MAX_IMAGE_WIDTH: int = 8192
    MAX_IMAGE_HEIGHT: int = 8192
    JOB_PROCESSING_TIMEOUT_SECONDS: int = 900

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @field_validator("JWT_SECRET")
    @classmethod
    def _jwt_secret_must_be_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters long. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if v.lower() in RESERVED_JWT_SECRETS:
            raise ValueError("JWT_SECRET can not be a placeholder value.")
        return v


settings = Settings()