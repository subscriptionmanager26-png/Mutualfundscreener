from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://mfscreener:mfscreener@localhost:5432/mfscreener"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "mfscreener"
    s3_secret_key: str = "mfscreener-secret"
    s3_bucket: str = "mf-raw-amfi"
    s3_region: str = "us-east-1"
    mfdata_base_url: str = "https://mfdata.in"
    risk_free_annual: float = 0.065
    active_nav_stale_days: int = 30
    amfi_navall_url: str = "https://portal.amfiindia.com/spages/NAVAll.txt"
    amfi_ter_url_template: str = (
        "https://www.amfiindia.com/api/populate-te-rdata-revised"
        "?MF_ID=All&Month={month}&strCat=-1&strType=-1&excel=true"
    )
    local_archive_dir: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
