from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The models tailor has prices for. Any other is refused at startup, since pricing it at another
# model's rates would understate its cost and let runs overspend their budget.
SONNET_MODEL = "claude-sonnet-5-5"
HAIKU_MODEL = "claude-haiku-5-5"
PRICED_MODELS = (SONNET_MODEL, HAIKU_MODEL)


class Settings(BaseSettings):
    # Compose passes unset optional values as empty strings.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    anthropic_api_key: str | None = None
    # Sonnet is enough for rewording within fixed rules, and the API's checks catch what it gets
    # wrong. Never Opus here.
    tailor_model: str = SONNET_MODEL
    tailor_max_tokens: int = Field(default=16_000, ge=1_000, le=32_000)
    tailor_effort: str = Field(default="medium", pattern="^(low|medium|high)$")
    # Seconds one model call may take before the SDK gives up, counted per try.
    tailor_request_timeout: float = 240.0
    log_level: str = "INFO"

    # The model the retired Scout agent used. Its runs' requests don't name their model,
    # so repricing them reads it from here. Never Opus here.
    scout_model: str = SONNET_MODEL
    scout_request_timeout: float = 300.0
    # Most searches in one request while finding companies. Kept small, so a run's total stays
    # close to its limits, and fixed, so the tool definitions (and the cache behind them) rarely
    # change.
    scout_searches_per_request: int = Field(default=5, ge=1)
    # Times discovery asks the model to carry on when it stops with companies still wanted. The
    # model often mistakes one step's search cap for the run's, so this is generous; the run's
    # own limits and budget still stop it, and a reply of DONE ends it at once.
    scout_max_nudges: int = Field(default=8, ge=0)
    # Prices in US dollars, for the run's budget. Per million tokens, and per web search, from
    # Anthropic's pricing page for Sonnet 5.5. Writes are the 5-minute cache rate, 1.25 times
    # input. Reads on Sonnet 5.5 are 0.05 times input. Check them when the model changes.
    price_input_per_mtok: float = 2.0
    price_output_per_mtok: float = 10.0
    price_cache_write_per_mtok: float = 2.5
    price_cache_read_per_mtok: float = 0.1
    price_per_web_search: float = 0.01
    # Haiku 5.5, per million tokens. A request whose prompt (input, cache writes and cache reads
    # together) passes the threshold pays the long-prompt prices for all of it.
    price_haiku_input_per_mtok: float = 0.10
    price_haiku_cache_write_per_mtok: float = 0.125
    price_haiku_cache_read_per_mtok: float = 0.01
    price_haiku_output_per_mtok: float = 0.50
    price_haiku_long_input_per_mtok: float = 0.50
    price_haiku_long_cache_write_per_mtok: float = 0.625
    price_haiku_long_cache_read_per_mtok: float = 0.05
    price_haiku_long_output_per_mtok: float = 2.50
    price_haiku_long_prompt_tokens: int = 100_000

    # Scout's judge: Haiku screens every posting code lets through, Sonnet takes a second
    # look at each one Haiku keeps, and Sonnet writes a brief when the user asks for one.
    pipeline_judge_model: str = HAIKU_MODEL
    pipeline_verify_model: str = SONNET_MODEL
    pipeline_brief_model: str = SONNET_MODEL
    pipeline_judge_max_tokens: int = Field(default=2_000, ge=500)
    pipeline_verify_max_tokens: int = Field(default=3_000, ge=500)
    pipeline_brief_max_tokens: int = Field(default=8_000, ge=1_000)
    # Characters of a fetched posting sent to the judge and the brief writer.
    pipeline_posting_chars: int = Field(default=30_000, ge=2_000)
    # The Companies engine: Haiku settles whether two similar names are one company.
    companies_match_model: str = HAIKU_MODEL
    companies_match_max_tokens: int = Field(default=2_000, ge=500)
    # And finds companies that fit a brief, searching the web.
    companies_discover_model: str = HAIKU_MODEL
    companies_discover_max_tokens: int = Field(default=8_000, ge=1_000)
    companies_discover_max_requests: int = Field(default=20, ge=1)
    # Rejection analysis: one call per rejected application, comparing the posting with the
    # resume sent and the fact sheet. Code has already graded the named requirements, so the
    # model only reads experience and writes the report: structured output Haiku handles well,
    # at a fraction of Sonnet's price. Never Opus here.
    reject_analysis_model: str = HAIKU_MODEL
    reject_analysis_max_tokens: int = Field(default=4_000, ge=1_000, le=16_000)
    reject_analysis_effort: str = Field(default="medium", pattern="^(low|medium|high)$")
    # Characters of a rejected posting sent to the model.
    reject_analysis_posting_chars: int = Field(default=30_000, ge=2_000)
    # Company research for rejections: Haiku with web search writes one snapshot of a company.
    # Searching and summarizing public pages into a fixed form is work Haiku does well; the
    # facts are checked in code afterwards. At most this many searches per company.
    company_research_model: str = HAIKU_MODEL
    company_research_max_searches: int = Field(default=6, ge=1, le=20)
    company_research_max_tokens: int = Field(default=8_000, ge=2_000, le=32_000)
    # Seconds and bytes allowed for one fetched page. The timeout is per read, so a slow page
    # also has a deadline for the whole download, redirects included.
    pipeline_fetch_timeout: float = 15.0
    pipeline_fetch_deadline: float = Field(default=45.0, gt=0)
    pipeline_fetch_max_bytes: int = Field(default=2_000_000, ge=10_000)

    # The compiler service, on an internal network with no route to the internet.
    compiler_url: str = "http://compiler:8000"
    # Two pdflatex passes at the compiler's own limit, with room for the hop.
    compiler_timeout_seconds: float = 90.0

    @model_validator(mode="after")
    def _priced_models_only(self) -> "Settings":
        for name in (
            "tailor_model",
            "scout_model",
            "pipeline_judge_model",
            "pipeline_verify_model",
            "pipeline_brief_model",
            "companies_match_model",
            "companies_discover_model",
            "reject_analysis_model",
            "company_research_model",
        ):
            model = getattr(self, name)
            if model not in PRICED_MODELS:
                raise ValueError(
                    f"{name.upper()} is {model}, which has no prices here. "
                    f"Use one of {', '.join(PRICED_MODELS)}, or add its prices first."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
