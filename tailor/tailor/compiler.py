"""Client for the compiler service, which turns LaTeX into a PDF. This service runs no LaTeX."""

import logging
from collections.abc import Callable

import httpx
from pydantic import ValidationError

from tailor.schemas import CompileResponse

logger = logging.getLogger(__name__)

# The most pages a tailored resume may have, so the compiler reports a longer one with its reason.
TAILORED_MAX_PAGES = 1

# Compiles the source, refusing more than max_pages pages.
Compiler = Callable[[str, int], CompileResponse]


class CompilerUnavailable(Exception):
    """The compiler service couldn't be reached or gave an answer that couldn't be used."""


def http_compiler(client: httpx.Client) -> Compiler:
    def compile_tex(tex: str, max_pages: int) -> CompileResponse:
        try:
            response = client.post("/compile", json={"tex": tex, "max_pages": max_pages})
        except httpx.TimeoutException as exc:
            logger.warning("The compiler service timed out")
            raise CompilerUnavailable("The compiler took too long to answer.") from exc
        except httpx.HTTPError as exc:
            logger.error("Could not reach the compiler service: %s", exc)
            raise CompilerUnavailable("The compiler couldn't be reached.") from exc
        if response.status_code != httpx.codes.OK:
            logger.error("The compiler service returned %s", response.status_code)
            raise CompilerUnavailable(f"The compiler returned an error ({response.status_code}).")
        try:
            return CompileResponse.model_validate_json(response.content)
        except ValidationError as exc:
            logger.error("The compiler service sent an unreadable answer")
            raise CompilerUnavailable("The compiler's answer couldn't be read.") from exc

    return compile_tex
