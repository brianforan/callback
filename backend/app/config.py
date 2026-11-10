import uuid
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Compose passes unset optional values as empty strings.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    database_url: str
    secret_key: str = Field(min_length=32)
    cookie_secure: bool = True
    registration_enabled: bool = False
    session_hours: int = 168
    # A resume is one or two pages, a few KB of LaTeX. The compiler service refuses more than this.
    max_resume_bytes: int = 65_536
    # An uploaded resume PDF, which carries fonts and images, so it's larger than its LaTeX.
    max_resume_pdf_bytes: int = 2_097_152
    stale_after_days: int = 14
    log_level: str = "INFO"
    default_user_id: uuid.UUID | None = None
    listings_cache_minutes: int = 360
    jooble_api_key: str | None = None
    findwork_api_key: str | None = None
    muse_api_key: str | None = None
    # Indeed and LinkedIn are read through JobSpy, which is slower than the other boards, so they
    # stay off until the owner of the server turns them on.
    indeed_enabled: bool = False
    linkedin_enabled: bool = False
    # The tailoring service, reached only by the worker on an internal network.
    tailor_url: str = "http://tailor:8000"
    # The compiler service, which turns each resume's LaTeX into a PDF. Also internal only.
    compiler_url: str = "http://compiler:8000"
    # Seconds one compile may take: two pdflatex passes, with room for the hop.
    compiler_timeout_seconds: float = 90.0
    # Seconds one attempt may take, including the model's own retries and two LaTeX passes.
    tailor_timeout_seconds: float = 900.0
    tailor_poll_seconds: float = 2.0
    # Daily limits and other settings that tune API spend are in the database: app.app_config.
    # Billing: the key the billing worker reads Anthropic's usage and cost reports with. An Admin
    # API key if given, otherwise the API key Callback itself uses, which works when it isn't tied
    # to a workspace. The key ID and workspace narrow the reports to what Callback spends; leave
    # them blank when nothing else uses the account.
    anthropic_admin_api_key: str | None = None
    anthropic_api_key: str | None = None
    billing_api_key_id: str | None = None
    billing_workspace_id: str | None = None
    billing_poll_seconds: float = Field(default=300.0, ge=60)
    # How often the daily cost report is fetched, and how many days back.
    billing_cost_sync_seconds: float = Field(default=3_600.0, ge=300)
    billing_cost_days: int = Field(default=31, ge=1, le=31)
    # Minutes to wait after a run ends before reading its usage, since reports trail by minutes.
    billing_report_delay_minutes: int = Field(default=10, ge=5)


def billing_key(settings: Settings) -> str | None:
    """The key the billing worker reads reports with: the Admin key, else Callback's own."""
    return settings.anthropic_admin_api_key or settings.anthropic_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
