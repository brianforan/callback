"""Read a resume PDF's text into a LaTeX-shaped outline, so it is analysed like a .tex resume.

A PDF has no markup, only positioned text. The outline keeps one line per printed line, marks
lines that read as section headings with \\section{} and bullet lines with \\item, and escapes
everything else. The existing LaTeX rules then find sections, dated roles and bullets in it.

Layout is guessed, not read: a two-column PDF can come out with its columns interleaved, and a
scanned or image-only PDF has no text at all. Outlines are only analysed, never compiled.
"""

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass

import pypdfium2 as pdfium

logger = logging.getLogger(__name__)

# A resume is one or two pages. Past this it's not a resume, and reading it costs too much.
MAX_PAGES = 5

# PDFium is not thread-safe, and FastAPI runs sync routes on a thread pool.
_PDFIUM_LOCK = threading.Lock()

# A soft hyphen where a word was split across lines. PDFium reports it as U+0002.
_SPLIT_WORD = re.compile(r"[\x02\xad][ \t]*\n[ \t]*")
_LINE_BREAKS = re.compile(r"\r\n?|\f")
# Bullet glyphs a resume line can start with, then the space after them.
_BULLET = re.compile(r"^(?:[•◦▪▫‣⁃∙●○■□►▸➢➤✓✔◆◇]|[-*–])\s+")
_TEX_SPECIAL = re.compile(r"([&%$#_{}])")
_TEX_DROPPED = re.compile(r"[\\~^]")

# The last word of a section heading, as in "Professional Experience" or "Technical Skills".
_HEADING_NOUNS = frozenset(
    """
    experience experiences employment history career projects project education academics
    skills skill summary profile objective certifications certification licenses awards honors
    honours publications volunteering volunteer leadership activities interests languages
    references coursework courses achievements qualifications training expertise involvement
    """.split()
)
_HEADING_CONNECTORS = frozenset({"&", "and", "/", "of", "+"})
_MAX_HEADING_WORDS = 5


class PdfReadError(ValueError):
    """The upload can't be read as a resume PDF. The message is shown to the user."""


@dataclass(frozen=True)
class PdfResume:
    outline: str
    pages: int


def _clean(text: str) -> str:
    """Fix the characters PDF text extraction gets wrong: ligatures, icon glyphs, split words."""
    text = _LINE_BREAKS.sub("\n", text)
    text = _SPLIT_WORD.sub("", text)
    # NFKC turns ligatures such as "ﬁ" back into letters, and odd spaces into plain ones.
    text = unicodedata.normalize("NFKC", text)
    # Private-use glyphs are icon fonts (phone, email, LinkedIn); other controls are noise.
    return "".join(
        char
        for char in text
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf", "Co", "Cs"}
    )


def _is_heading(line: str) -> bool:
    text = line.strip().rstrip(":").strip()
    words = text.split()
    if not words or len(words) > _MAX_HEADING_WORDS or any(char.isdigit() for char in text):
        return False
    if words[-1].lower() not in _HEADING_NOUNS:
        return False
    if not all(word.isalpha() or word in _HEADING_CONNECTORS for word in words):
        return False
    # "Senior engineering leadership" in a bullet isn't a heading; "LEADERSHIP" or "Leadership" is.
    return text.isupper() or all(
        word[0].isupper() or word.lower() in _HEADING_CONNECTORS for word in words
    )


def _escape(text: str) -> str:
    return _TEX_SPECIAL.sub(r"\\\1", _TEX_DROPPED.sub(" ", text))


def outline_from_text(text: str) -> str:
    """Turn extracted PDF text into the LaTeX-shaped outline that the resume parsers read."""
    lines = []
    for raw in _clean(text).split("\n"):
        line = " ".join(raw.split())
        if not line:
            continue
        if _is_heading(line):
            lines.append(f"\\section{{{_escape(line.rstrip(':').strip())}}}")
            continue
        bullet = _BULLET.match(line)
        if bullet:
            rest = line[bullet.end() :]
            if rest:
                lines.append(f"\\item {_escape(rest)}")
            continue
        lines.append(_escape(line))
    return "\n".join(lines) + "\n" if lines else ""


def read_pdf(raw: bytes) -> PdfResume:
    """Extract a resume PDF's text as an outline. Raises PdfReadError when it can't be used."""
    with _PDFIUM_LOCK:
        try:
            document = pdfium.PdfDocument(raw)
        except pdfium.PdfiumError as exc:
            logger.info("Refused a resume PDF that PDFium can't open: %s", exc)
            raise PdfReadError(
                "This PDF can't be opened. It may be damaged or password-protected."
            ) from exc
        try:
            pages = len(document)
            if pages == 0:
                raise PdfReadError("This PDF has no pages")
            if pages > MAX_PAGES:
                raise PdfReadError(f"A resume PDF can be at most {MAX_PAGES} pages")
            texts = []
            for index in range(pages):
                page = document[index]
                textpage = page.get_textpage()
                try:
                    texts.append(textpage.get_text_bounded())
                finally:
                    textpage.close()
                    page.close()
        except pdfium.PdfiumError as exc:
            logger.warning("PDFium failed while reading a resume PDF: %s", exc)
            raise PdfReadError("The text in this PDF can't be read") from exc
        finally:
            document.close()

    outline = outline_from_text("\n".join(texts))
    if not outline.strip():
        raise PdfReadError(
            "This PDF has no selectable text. It may be a scanned image; "
            "export it from the original document instead."
        )
    return PdfResume(outline=outline, pages=pages)
