import tempfile
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Compose passes unset optional values as empty strings.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    log_level: str = "INFO"

    # A resume is one or two pages, which is a few KB of source. Anything bigger is not a resume.
    max_source_bytes: int = Field(default=64 * 1024, ge=1_024)
    # The most pages any caller may ask for. A request can lower this but never raise it.
    max_pages: int = Field(default=2, ge=1)

    # pdflatex limits for one compile pass. The memory cap is on address space, so it sits above
    # what a resume really uses (about 100 to 200 MB) and below what a small host can spare.
    latex_timeout_seconds: float = 30.0
    latex_cpu_seconds: int = 20
    latex_memory_bytes: int = 400 * 1024 * 1024
    latex_file_bytes: int = 16 * 1024 * 1024
    # Every compile gets its own temporary folder under this one, the only writable place.
    latex_work_root: str = Field(default_factory=tempfile.gettempdir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
