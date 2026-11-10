"""HTTP front of the compiler service: LaTeX in, PDF out.

It holds no secrets, has no database and no route to the internet, and makes no model call. The
worker and the tailor service reach it over an internal network. A source that is too large, fails
to compile or has too many pages comes back as ok=false with the reason. Nothing is stored here.
"""

import base64
import logging

from fastapi import FastAPI

from compiler.config import get_settings
from compiler.latex import LatexError, compile_pdf
from compiler.schemas import CompileRequest, CompileResponse

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Callback compiler", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _page_limit_reason(pages: int, limit: int) -> str:
    wanted = "exactly 1" if limit == 1 else f"at most {limit}"
    return f"The PDF is {pages} pages. It must be {wanted}."


@app.post("/compile", response_model=CompileResponse)
def compile_resume(request: CompileRequest) -> CompileResponse:
    settings = get_settings()
    size = len(request.tex.encode("utf-8"))
    if size > settings.max_source_bytes:
        return CompileResponse(
            ok=False,
            reasons=[
                f"The .tex is {size // 1024} KB. Resumes can be at most "
                f"{settings.max_source_bytes // 1024} KB."
            ],
        )
    limit = min(request.max_pages or settings.max_pages, settings.max_pages)
    try:
        compiled = compile_pdf(request.tex, settings)
    except LatexError as exc:
        logger.info("Resume didn't compile: %s", exc)
        return CompileResponse(ok=False, reasons=[str(exc)])
    if compiled.pages > limit:
        return CompileResponse(ok=False, reasons=[_page_limit_reason(compiled.pages, limit)])
    return CompileResponse(
        ok=True,
        pdf_base64=base64.b64encode(compiled.pdf).decode("ascii"),
        pages=compiled.pages,
    )
