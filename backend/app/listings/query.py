"""Listing search syntax: "exact phrases", OR, (groups), -exclusions, title: and company:."""

import re
from dataclasses import dataclass

from app.postings.parser import technology_in_text

_TOKEN = re.compile(
    r"""(?P<open>-?\()|(?P<close>\))|(?P<negate>-)?(?:(?P<field>title|company):)?"""
    r"""(?:"(?P<phrase>[^"]*)"?|(?P<word>[^\s()"]+))""",
    re.I,
)


@dataclass(frozen=True)
class Document:
    title: str
    company: str
    text: str


@dataclass(frozen=True)
class _Term:
    value: str
    field: str

    def matches(self, doc: Document, trust_text: bool) -> bool:
        if self.field == "title":
            return technology_in_text(self.value, doc.title)
        if self.field == "company":
            return technology_in_text(self.value, doc.company)
        # A board that ran the search itself may have matched text it only sent a snippet of.
        text = f"{doc.title}\n{doc.company}\n{doc.text}"
        return trust_text or technology_in_text(self.value, text)

    def board_words(self) -> list[str]:
        return [self.value]


@dataclass(frozen=True)
class _Not:
    child: "_Node"

    def matches(self, doc: Document, trust_text: bool) -> bool:
        # An exclusion is always checked, since finding the word is proof however short the text.
        return not self.child.matches(doc, False)

    def board_words(self) -> list[str]:
        return []


@dataclass(frozen=True)
class _All:
    children: tuple["_Node", ...]

    def matches(self, doc: Document, trust_text: bool) -> bool:
        return all(child.matches(doc, trust_text) for child in self.children)

    def board_words(self) -> list[str]:
        return [word for child in self.children for word in child.board_words()]


@dataclass(frozen=True)
class _Any:
    children: tuple["_Node", ...]

    def matches(self, doc: Document, trust_text: bool) -> bool:
        return any(child.matches(doc, trust_text) for child in self.children)

    def board_words(self) -> list[str]:
        # Boards can't be relied on to understand OR, so they search the first choice.
        return self.children[0].board_words()


_Node = _Term | _Not | _All | _Any


class _Parser:
    """Recursive descent over the tokens. Stray brackets are ignored rather than rejected."""

    def __init__(self, source: str) -> None:
        self.tokens = list(_TOKEN.finditer(source))
        self.position = 0

    def _peek(self) -> re.Match[str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _is_operator(self, token: re.Match[str] | None, name: str) -> bool:
        return (
            token is not None
            and token.group("word") == name
            and not token.group("negate")
            and not token.group("field")
        )

    def parse_all(self, nested: bool = False) -> _Node | None:
        children: list[_Node] = []
        while (token := self._peek()) is not None:
            if token.group("close"):
                self.position += 1
                if nested:
                    break
                continue
            if self._is_operator(token, "AND"):
                self.position += 1
                continue
            child = self._parse_any()
            if child is not None:
                children.append(child)
        if not children:
            return None
        return children[0] if len(children) == 1 else _All(tuple(children))

    def _parse_any(self) -> _Node | None:
        children = []
        first = self._parse_unit()
        if first is not None:
            children.append(first)
        while self._is_operator(self._peek(), "OR"):
            self.position += 1
            following = self._parse_unit()
            if following is not None:
                children.append(following)
        if not children:
            return None
        return children[0] if len(children) == 1 else _Any(tuple(children))

    def _parse_unit(self) -> _Node | None:
        token = self._peek()
        if token is None or token.group("close"):
            return None
        self.position += 1
        opened = token.group("open")
        if opened:
            group = self.parse_all(nested=True)
            if group is None:
                return None
            return _Not(group) if opened.startswith("-") else group
        value = " ".join((token.group("phrase") or token.group("word") or "").split())
        if not value or self._is_bare_operator(value, token):
            return None
        term = _Term(value, (token.group("field") or "text").lower())
        return _Not(term) if token.group("negate") else term

    @staticmethod
    def _is_bare_operator(value: str, token: re.Match[str]) -> bool:
        # "python OR" has nothing after the operator, and it shouldn't be searched as a word.
        return value in {"AND", "OR"} and not token.group("phrase") and not token.group("field")


@dataclass(frozen=True)
class ListingQuery:
    root: _Node | None
    board_keywords: str

    def matches(self, title: str, company: str, text: str, trust_text: bool = False) -> bool:
        if self.root is None:
            return True
        return self.root.matches(Document(title, company, text), trust_text)

    def matches_title(self, title: str, company: str) -> bool:
        return self.matches(title, company, "")


def parse_query(source: str) -> ListingQuery:
    root = _Parser(source).parse_all()
    words = root.board_words() if root is not None else []
    return ListingQuery(root, " ".join(words))
