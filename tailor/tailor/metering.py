"""Reading model responses and pricing them, shared by the Scout agent and the pipeline.

Prices come from tailor's settings, by exact model id. Settings refuses a model it has no prices
for, so a new model can't be priced at another model's rates by accident.
"""

import re
from typing import Any

from tailor.config import HAIKU_MODEL, SONNET_MODEL, Settings
from tailor.schemas import RequestCost, SearchUsage

_MILLION = 1_000_000
# "DONE", "Done.", "done - nothing else fits": a reply whose first word is done.
_DONE = re.compile(r"^\W*done\b", re.IGNORECASE)


def get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from an SDK block or a plain dict alike."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def fence(text: str, tag: str) -> str:
    """Stop text from closing the tag it's wrapped in early."""
    return text.replace(f"</{tag}", f"<\\/{tag}")


def _failed_searches(content: list[Any]) -> int:
    """Web searches in a response that came back as an error, such as one past max_uses.
    Anthropic doesn't bill them."""
    return sum(
        1
        for block in content
        if get(block, "type") == "web_search_tool_result"
        # A result's content is a list of hits, or an error object.
        and not isinstance(get(block, "content"), list)
    )


def usage_of(message: Any) -> SearchUsage:
    """One response's token counts and the server tool uses it's billed for."""
    usage = get(message, "usage")
    if usage is None:
        return SearchUsage()
    server = get(usage, "server_tool_use")
    searches = (get(server, "web_search_requests", 0) or 0) if server else 0
    failed = _failed_searches(list(get(message, "content", []) or []))
    return SearchUsage(
        input_tokens=get(usage, "input_tokens", 0) or 0,
        output_tokens=get(usage, "output_tokens", 0) or 0,
        cache_creation_input_tokens=get(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=get(usage, "cache_read_input_tokens", 0) or 0,
        web_search_requests=max(0, searches - failed),
        web_fetch_requests=(get(server, "web_fetch_requests", 0) or 0) if server else 0,
    )


def said_done(content: list[Any]) -> bool:
    """Whether the model's last words in this response start with "done"."""
    texts = [
        text
        for block in content
        if get(block, "type") == "text" and (text := str(get(block, "text", "") or "").strip())
    ]
    return bool(texts) and _DONE.match(texts[-1]) is not None


def _token_prices(usage: SearchUsage, settings: Settings, model: str) -> tuple[float, ...]:
    """Input, cache write, cache read and output prices per million tokens for this request."""
    if model == SONNET_MODEL:
        return (
            settings.price_input_per_mtok,
            settings.price_cache_write_per_mtok,
            settings.price_cache_read_per_mtok,
            settings.price_output_per_mtok,
        )
    if model != HAIKU_MODEL:
        raise ValueError(f"No prices are set for {model}")
    # A request that searches runs one model step per search and a last one, each re-reading the
    # conversation so far, and Anthropic prices each step by its own prompt. The response gives
    # only the total, so the average step's prompt decides the rate. Checked against the bill:
    # pricing the total instead overstated Haiku by half.
    total = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
    prompt = total / (usage.web_search_requests + 1)
    if prompt > settings.price_haiku_long_prompt_tokens:
        return (
            settings.price_haiku_long_input_per_mtok,
            settings.price_haiku_long_cache_write_per_mtok,
            settings.price_haiku_long_cache_read_per_mtok,
            settings.price_haiku_long_output_per_mtok,
        )
    return (
        settings.price_haiku_input_per_mtok,
        settings.price_haiku_cache_write_per_mtok,
        settings.price_haiku_cache_read_per_mtok,
        settings.price_haiku_output_per_mtok,
    )


def cost_parts(
    usage: SearchUsage, settings: Settings, model: str | None = None
) -> dict[str, float]:
    """What each part of one request's usage cost in US dollars, at the model's prices.

    The Scout model is the default. Haiku is priced by the request's prompt length, as
    Anthropic bills it.
    """
    input_price, write_price, read_price, output_price = _token_prices(
        usage, settings, model or settings.scout_model
    )
    return {
        "input": usage.input_tokens * input_price / _MILLION,
        "output": usage.output_tokens * output_price / _MILLION,
        "cache_write": usage.cache_creation_input_tokens * write_price / _MILLION,
        "cache_read": usage.cache_read_input_tokens * read_price / _MILLION,
        "web_search": usage.web_search_requests * settings.price_per_web_search,
    }


def message_cost(usage: SearchUsage, settings: Settings, model: str | None = None) -> float:
    """What one request's usage cost in US dollars, at the model's prices."""
    return sum(cost_parts(usage, settings, model).values())


class Meter:
    """Usage, cost by part and per-request costs across one call's model requests."""

    def __init__(self, settings: Settings, model: str) -> None:
        self.settings = settings
        self.model = model
        self.usage = SearchUsage()
        self.costs: dict[str, float] = {}
        self.request_costs: list[RequestCost] = []
        self.last_cost = 0.0

    @property
    def spent(self) -> float:
        return sum(self.costs.values())

    def add(self, usage: SearchUsage, seconds: float) -> None:
        self.usage = SearchUsage(
            **{
                name: getattr(self.usage, name) + getattr(usage, name)
                for name in SearchUsage.model_fields
            }
        )
        parts = cost_parts(usage, self.settings, self.model)
        for part, dollars in parts.items():
            self.costs[part] = self.costs.get(part, 0.0) + dollars
        self.last_cost = sum(parts.values())
        self.request_costs.append(
            RequestCost(
                **usage.model_dump(),
                request=len(self.request_costs) + 1,
                cost_usd=round(self.last_cost, 6),
                seconds=round(seconds, 1),
            )
        )

    def fields(self) -> dict[str, Any]:
        """The response fields every stage shares: model, usage, cost and its breakdown."""
        return {
            "model": self.model,
            "usage": self.usage,
            "cost_usd": round(self.spent, 6),
            "cost_breakdown": {part: round(value, 6) for part, value in self.costs.items()},
            "request_costs": self.request_costs,
        }

    def spent_json(self) -> dict[str, Any]:
        """fields() as plain JSON, to send with an error so the caller still counts the cost."""
        fields = self.fields()
        return {
            **fields,
            "usage": self.usage.model_dump(),
            "request_costs": [cost.model_dump() for cost in self.request_costs],
        }
