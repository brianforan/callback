"""Compile untrusted LaTeX into a PDF with pdflatex, as tightly fenced as the container allows.

The source comes from a user upload and a model's rewrite of it, so it's treated as hostile:
shell escape is off, kpathsea may only read and write inside the work folder ("paranoid"), the
font generators that would run helper programs are off, and prlimit caps CPU time, memory, open
files and file size. A wall-clock timeout covers anything prlimit doesn't. pdflatex has no
network access of its own once shell escape is off.
"""

import logging
import os
import re
import subprocess  # noqa: S404 - runs pdflatex with a fixed argument list, never a shell
import tempfile
from dataclasses import dataclass
from pathlib import Path

from compiler.config import Settings

logger = logging.getLogger(__name__)

_SOURCE_NAME = "resume"
_LOG_LIMIT = 1_000_000
_PAGES = re.compile(r"Output written on \S+ \((\d+) pages?,")
_ERROR_LINE = re.compile(r"^(?:\S+:\d+: |! )(.+)$", re.MULTILINE)
_MAX_ERROR_LENGTH = 300


class LatexError(Exception):
    """The source didn't compile. The message is safe to show to the user."""


@dataclass(frozen=True)
class CompiledPdf:
    pdf: bytes
    pages: int


def _environment(work: Path) -> dict[str, str]:
    """A minimal environment: nothing from the service's own, such as the API key, leaks in."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(work),
        "TEXMFVAR": str(work / "texmf-var"),
        "openin_any": "p",
        "openout_any": "p",
        "shell_escape": "f",
        "MKTEXTEX": "0",
        "MKTEXTFM": "0",
        "MKTEXMF": "0",
        "MKTEXPK": "0",
        "MKOCP": "0",
        "MKOFM": "0",
        "LANG": "C.UTF-8",
    }


def _command(settings: Settings) -> list[str]:
    return [
        "prlimit",
        f"--cpu={settings.latex_cpu_seconds}",
        f"--as={settings.latex_memory_bytes}",
        f"--fsize={settings.latex_file_bytes}",
        "--nofile=256",
        "--core=0",
        "--",
        "pdflatex",
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        f"{_SOURCE_NAME}.tex",
    ]


def _read_log(work: Path) -> str:
    log = work / f"{_SOURCE_NAME}.log"
    if not log.exists():
        return ""
    with log.open("rb") as handle:
        return handle.read(_LOG_LIMIT).decode("utf-8", errors="replace")


def _first_error(log: str) -> str:
    match = _ERROR_LINE.search(log)
    if match is None:
        return "pdflatex stopped without saying why"
    return match.group(1).strip()[:_MAX_ERROR_LENGTH]


def _run_pass(work: Path, settings: Settings) -> None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
            _command(settings),
            cwd=work,
            env=_environment(work),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=settings.latex_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LatexError(
            f"pdflatex took longer than {settings.latex_timeout_seconds:g} seconds"
        ) from exc
    except OSError as exc:
        logger.exception("Could not start pdflatex")
        raise LatexError("pdflatex couldn't be started on the compiler service") from exc
    if result.returncode != 0:
        raise LatexError(f"LaTeX didn't compile: {_first_error(_read_log(work))}")


def compile_pdf(source: str, settings: Settings) -> CompiledPdf:
    """Run pdflatex twice, as the template's references and hyperref need, and return the PDF."""
    with tempfile.TemporaryDirectory(prefix="tex-", dir=settings.latex_work_root) as folder:
        work = Path(folder)
        (work / f"{_SOURCE_NAME}.tex").write_text(source, encoding="utf-8")
        for _ in range(2):
            _run_pass(work, settings)
        log = _read_log(work)
        pdf_path = work / f"{_SOURCE_NAME}.pdf"
        # The source can write any line to the log, so only the last count is pdfTeX's own.
        counts = _PAGES.findall(log)
        if not counts or not pdf_path.exists():
            raise LatexError("LaTeX compiled but produced no pages")
        return CompiledPdf(pdf=pdf_path.read_bytes(), pages=int(counts[-1]))
