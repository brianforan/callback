"""The sandbox: a normal resume compiles, and hostile LaTeX can't reach outside its folder."""

from pathlib import Path

import pytest

from compiler.latex import LatexError, compile_pdf

_DOCUMENT = "\\documentclass{article}\\begin{document}%s\\end{document}"


def test_resume_compiles_to_one_page(resume_tex, settings):
    compiled = compile_pdf(resume_tex, settings)

    assert compiled.pages == 1
    assert compiled.pdf.startswith(b"%PDF")


def test_counts_every_page(settings):
    compiled = compile_pdf(_DOCUMENT % "One\\newpage Two", settings)

    assert compiled.pages == 2


def test_a_fake_page_count_in_the_log_is_ignored(settings):
    """A source can write any line to the log, so only pdfTeX's own last line counts."""
    fake = "\\typeout{Output written on resume.pdf (1 page, 999 bytes).}"
    compiled = compile_pdf(_DOCUMENT % (fake + "One\\newpage Two\\newpage Three"), settings)

    assert compiled.pages == 3


def test_reports_the_first_error(settings):
    with pytest.raises(LatexError, match="Undefined control sequence"):
        compile_pdf(_DOCUMENT % "\\notacommand", settings)


def test_shell_escape_is_off(tmp_path, settings):
    marker = tmp_path / "ran"
    source = _DOCUMENT % f"\\immediate\\write18{{touch {marker}}}Text"

    compile_pdf(source, settings)

    assert not marker.exists()


def test_cannot_read_absolute_paths(settings):
    with pytest.raises(LatexError):
        compile_pdf(_DOCUMENT % "\\input{/etc/passwd}", settings)


def test_cannot_write_outside_the_work_folder(settings):
    escape = Path(settings.latex_work_root) / "escaped.txt"
    source = _DOCUMENT % (
        "\\newwrite\\out\\immediate\\openout\\out=../escaped.txt"
        "\\immediate\\write\\out{owned}\\immediate\\closeout\\out Text"
    )

    try:
        compile_pdf(source, settings)
    except LatexError:
        pass

    assert not escape.exists()


def test_service_environment_does_not_leak(settings):
    # \input of an environment variable's value would need shell escape or kpathsea expansion of
    # a variable that isn't passed in. The canary must not reach the PDF either way.
    compiled = compile_pdf(_DOCUMENT % "\\detokenize{$LEAK_CANARY}", settings)

    assert b"canary-value-not-for-latex" not in compiled.pdf


def test_endless_loop_is_stopped(settings):
    fast = settings.model_copy(update={"latex_timeout_seconds": 3, "latex_cpu_seconds": 2})

    with pytest.raises(LatexError):
        compile_pdf(_DOCUMENT % "\\def\\loop{\\loop}\\loop", fast)


def test_work_folder_is_removed(resume_tex, settings):
    before = set(Path(settings.latex_work_root).glob("tex-*"))

    compile_pdf(resume_tex, settings)

    assert set(Path(settings.latex_work_root).glob("tex-*")) == before
