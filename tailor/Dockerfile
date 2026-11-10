# The tailoring service: one model call, then a check that the result compiles through the compiler
# service. It holds the Anthropic key and no database credentials, and it runs no LaTeX itself.

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY pyproject.toml ./
COPY tailor ./tailor

FROM base AS test
RUN pip install ".[dev]"
COPY tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
RUN pip install . && useradd --system --uid 10002 --no-create-home tailor
USER tailor
EXPOSE 8000
CMD ["uvicorn", "tailor.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
