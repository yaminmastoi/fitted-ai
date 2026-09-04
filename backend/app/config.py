from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    environment: str = "development"
    supabase_url: str = "https://invalid.local"
    supabase_service_role_key: str = "development-only"
    supabase_jwt_audience: str = "authenticated"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/postgres"
    redis_url: str = "redis://localhost:6379/0"
    fal_key: str = ""
    fal_model: str = "fal-ai/fashn/tryon/v1.6"
    frontend_urls: str = "http://localhost:5173"
    guest_cookie_secret: str = Field(default="development-cookie-secret-change-me-32")
    ip_hash_secret: str = Field(default="development-ip-secret-change-me-32")
    payment_provider: str = "manual_test"
    payment_secret: str = ""
    payment_webhook_secret: str = ""
    storage_bucket: str = "tryon-private"
    trusted_proxy_count: int = 0
    csp_script_origins: str = ""
    csp_frame_origins: str = ""
    csp_connect_origins: str = ""

    @property
    def origins(self) -> list[str]:
        return [x.strip().rstrip("/") for x in self.frontend_urls.split(",") if x.strip()]

    @property
    def production(self) -> bool:
        return self.environment.lower() == "production"

    @staticmethod
    def csp_list(value: str) -> str:
        return " ".join(origin.strip() for origin in value.split(",") if origin.strip())


@lru_cache
def settings() -> Settings:
    return Settings()
