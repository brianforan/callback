FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app

FROM base AS test
RUN pip install ".[dev]"
COPY tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
RUN pip install . && useradd --system --uid 10001 --no-create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
