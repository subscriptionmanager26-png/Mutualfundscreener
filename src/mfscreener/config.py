from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://mfscreener:mfscreener@localhost:5432/mfscreener"
    local_archive_dir: str = ".data/raw-amfi"
    mfdata_base_url: str = "https://mfdata.in"
    risk_free_annual: float = 0.065
    active_nav_stale_days: int = 30
    amfi_navall_url: str = "https://portal.amfiindia.com/spages/NAVAll.txt"
    amfi_ter_url_template: str = (
        "https://www.amfiindia.com/api/populate-te-rdata-revised"
        "?MF_ID=All&Month={month}&strCat=-1&strType=-1&excel=true"
    )
    mfapi_base_url: str = "https://api.mfapi.in"
    finapi_base_url: str = "https://finapi.upvaly.com"
    finapi_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
