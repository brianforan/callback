from pydantic import BaseModel, Field


class CompileRequest(BaseModel):
    """LaTeX to compile as it is. The size limit that matters is checked against the settings."""

    tex: str = Field(min_length=1, max_length=200_000)
    # Fewer pages than the service allows, such as exactly one for a tailored resume.
    max_pages: int | None = Field(default=None, ge=1)


class CompileResponse(BaseModel):
    """The PDF, or why there isn't one."""

    ok: bool
    pdf_base64: str | None = None
    pages: int | None = None
    reasons: list[str] = Field(default_factory=list)
