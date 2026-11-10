# The compiler service: a sandboxed pdflatex behind one HTTP route. It has no secrets, no database
# credentials and, in compose, no route to the internet.

# TeX Live from the frozen 2025 repository, so a rebuild installs the same package versions.
# Only the packages the resume template needs are installed, plus what they load themselves.
FROM python:3.13-slim AS texlive
ARG TL_REPOSITORY=https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2025/tlnet-final
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl perl xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSL "$TL_REPOSITORY/install-tl-unx.tar.gz" | tar -xz --strip-components=1 \
    && printf '%s\n' \
        'selected_scheme scheme-infraonly' \
        'TEXDIR /opt/texlive' \
        'TEXMFLOCAL /opt/texlive/texmf-local' \
        'TEXMFSYSCONFIG /opt/texlive/texmf-config' \
        'TEXMFSYSVAR /opt/texlive/texmf-var' \
        'instopt_adjustpath 0' \
        'tlpdbopt_autobackup 0' \
        'tlpdbopt_install_docfiles 0' \
        'tlpdbopt_install_srcfiles 0' \
        > texlive.profile \
    && perl ./install-tl --profile=texlive.profile --repository="$TL_REPOSITORY" \
    && ln -s /opt/texlive/bin/* /opt/texlive/bin/current \
    && /opt/texlive/bin/current/tlmgr install \
        latex-bin \
        ec geometry microtype etoolbox xcolor graphics graphics-cfg graphics-def \
        enumitem titlesec \
        sourcesanspro fontaxes mweights xkeyval ly1 \
        hyperref url kvoptions kvsetkeys kvdefinekeys ltxcmds pdftexcmds infwarerr \
        pdfescape hycolor auxhook gettitlestring intcalc bitset bigintcalc \
        rerunfilecheck uniquecounter refcount letltxmacro stringenc zapfding \
        atbegshi atveryend iftex etexcmds \
    && rm -rf /opt/texlive/tlpkg/backups /opt/texlive/texmf-var/web2c/*.log /build

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/texlive/bin/current:$PATH
COPY --from=texlive /opt/texlive /opt/texlive
WORKDIR /srv
COPY pyproject.toml ./
COPY compiler ./compiler

FROM base AS test
RUN pip install ".[dev]"
COPY tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
RUN pip install . && useradd --system --uid 10003 --no-create-home compiler
USER compiler
EXPOSE 8000
CMD ["uvicorn", "compiler.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
