"""Pure text functions: LaTeX to plain text, term counting, and text similarity."""

import math
import re
from collections import Counter

DOCUMENT_BODY = re.compile(r"\\begin\{document\}(.*?)(?:\\end\{document\}|\Z)", re.DOTALL)
LATEX_COMMENT = re.compile(r"(?<!\\)%.*")
_ENVIRONMENT = re.compile(r"\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*\])?(?:\{(?:[^{}]|\{[^{}]*\})*\})*")
# Commands whose arguments are layout or metadata rather than resume content.
_DROP_ALL_ARGS = re.compile(
    r"\\(?:vspace|hspace|setlength|addtolength|usepackage|documentclass|input|include"
    r"|includegraphics|url|color|pagestyle|thispagestyle|setlist|fontsize|rule)(?![a-zA-Z])\*?"
    r"(?:\[[^\]]*\])*(?:\{[^{}]*\})*"
)
# Commands whose first argument is a target or color and whose second is visible text.
_DROP_FIRST_ARG = re.compile(r"\\(?:href|textcolor)\{[^{}]*\}")
# A line break can carry a spacing argument, as in \\[0.12em].
_LINE_BREAK = re.compile(r"\\\\\*?(?:\[[^\]]*\])?")
_ESCAPED_CHAR = re.compile(r"\\([&%$#_{}])")
_COMMAND = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?")
_LEFTOVER = re.compile(r"\\.|[{}~$^]|-{2,}")
# Bare domains are limited to these endings, so tech terms such as node.js and asp.net survive.
_URL_OR_EMAIL = re.compile(r"https?://\S+|www\.\S+|\S+@\S+|\S+\.(?:com|org)(?:/\S*)?(?!\w)")
_TOKEN = re.compile(r"[a-z][a-z0-9+#.-]*[a-z0-9+#]")

STOPWORDS = frozenset(
    """
    a about above across after all also am an and any are as at be been being but by can could did
    do does done during each for from had has have he her his how i if in including into is it its
    me more most my of on one or our out over she so such than that the their them then there these
    they this those through to too two under up us using via was we were what when where which while
    who will with within would you your
    against before below between both no not off only other same should some until very without
    experience education skills projects summary present references work
    jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july
    august september october november december
    """.split()
)


def tex_to_text(tex: str) -> str:
    """Reduce LaTeX source to the words a reader of the compiled resume would see."""
    uncommented = LATEX_COMMENT.sub("", tex)
    body = DOCUMENT_BODY.search(uncommented)
    text = body.group(1) if body else uncommented
    for pattern in (_LINE_BREAK, _ENVIRONMENT, _DROP_ALL_ARGS, _DROP_FIRST_ARG):
        text = pattern.sub(" ", text)
    text = _ESCAPED_CHAR.sub(r"\1", text)
    text = _COMMAND.sub(" ", text)
    text = _LEFTOVER.sub(" ", text)
    return " ".join(text.split())


def term_counts(text: str) -> Counter[str]:
    """Count meaningful lowercase terms, keeping tech tokens such as c++, c# and node.js."""
    cleaned = _URL_OR_EMAIL.sub(" ", text.lower())
    return Counter(token for token in _TOKEN.findall(cleaned) if token not in STOPWORDS)


def cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    """Cosine similarity of two term-count vectors: 1 is the same mix of terms, 0 shares none."""
    if not left or not right:
        return 0.0
    dot = sum(count * right[term] for term, count in left.items() if term in right)
    norm = math.sqrt(sum(c * c for c in left.values())) * math.sqrt(
        sum(c * c for c in right.values())
    )
    return min(dot / norm, 1.0)
