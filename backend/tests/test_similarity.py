from collections import Counter

import pytest

from app.similarity import cosine_similarity, term_counts, tex_to_text

SAMPLE = r"""
\documentclass[11pt]{article}
\usepackage{geometry}
\newcommand{\secret}{Preamble words are ignored}
\begin{document}
% a comment that should vanish
\section*{Jane Doe} \href{https://example.com/me}{Portfolio}
\begin{itemize}[leftmargin=*]
  \item Built \textbf{Python} services in C++ \& node.js, raising revenue 20\%
  \vspace{-2pt}
\end{itemize}
\begin{tabular*}{\textwidth}{l@{\extracolsep{\fill}}r}
  Staff Engineer & 2024
\end{tabular*}
\end{document}
"""


def test_tex_to_text_keeps_visible_words_only():
    text = tex_to_text(SAMPLE)
    for expected in ("Jane Doe", "Portfolio", "Python", "C++", "node.js", "20%", "Staff Engineer"):
        assert expected in text
    for absent in ("comment", "Preamble", "example.com", "leftmargin", "vspace", "textwidth", "\\"):
        assert absent not in text


def test_tex_to_text_handles_fragment_without_document_env():
    assert tex_to_text(r"\textbf{Hello} world") == "Hello world"


def test_term_counts_keeps_tech_terms_and_drops_noise():
    counts = term_counts(
        "Built C++ and C# with node.js and Python, python at me@example.com in 2024"
    )
    assert counts["python"] == 2
    assert {"c++", "c#", "node.js", "built"} <= set(counts)
    assert not {"and", "with", "in", "me@example.com", "example.com", "2024"} & set(counts)


def test_cosine_similarity_bounds():
    left = Counter(python=2, sql=1)
    assert cosine_similarity(left, left) == pytest.approx(1.0)
    assert cosine_similarity(left, Counter(java=4)) == 0.0
    assert cosine_similarity(Counter(), left) == 0.0
    assert 0 < cosine_similarity(left, Counter(python=1, java=1)) < 1


def test_tex_to_text_removes_line_break_spacing():
    source = r"\begin{document}{\LARGE Jane}\\[0.12em] Engineer\\*[2pt] Lead\\ Dev\end{document}"
    text = tex_to_text(source)
    assert text == "Jane Engineer Lead Dev"


def test_term_counts_drops_bare_domains_but_keeps_tech_with_dots():
    counts = term_counts("linkedin.com/in/jane and acme.org with node.js, asp.net and socket.io")
    assert not {"linkedin.com", "jane", "acme.org"} & set(counts)
    assert {"node.js", "asp.net", "socket.io"} <= set(counts)


def test_term_counts_drops_common_connecting_words():
    assert "against" not in term_counts("Tested against design specifications")
