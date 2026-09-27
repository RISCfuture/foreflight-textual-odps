"""Tokens of normalized ODP text and a cursor over them.

Every non-space character belongs to some token, so text the grammar does not
know always surfaces as an unmatched phrase rather than being skipped.
`TokenStream` holds the cursor primitives the recursive-descent grammar is
built from; `ParseError` is what they raise.
"""

from __future__ import annotations

import dataclasses
import re


class ParseError(Exception):
    """Text the grammar cannot consume.

    ``signature`` is a short, airport-independent phrase for grouping
    identical failures; ``detail`` quotes the text; ``position`` is the
    character offset where parsing stopped.
    """

    def __init__(self, signature: str, detail: str = "", position: int = 0):
        super().__init__(f"{signature} at {position}: {detail}")
        self.signature = signature
        self.detail = detail
        self.position = position


_TOKEN = re.compile(
    r"\.\.\.|R-\d+|\d+(?:\.\d+)?[A-Za-z]*|[A-Za-z][A-Za-z0-9]*|\S",
)
NUMBER = re.compile(r"\d+(?:\.\d+)?")
_UPPERCASE_WORD = re.compile(r"[A-Z]{2,}")
_RESERVED_WORDS = frozenset(
    [
        "ATC",
        "CCW",
        "CW",
        "DER",
        "DME",
        "IFR",
        "KIAS",
        "LT",
        "MCA",
        "MEA",
        "MSL",
        "NA",
        "NDB",
        "RNAV",
        "RT",
        "VCOA",
        "VOR",
        "VORTAC",
    ]
)
_SIGNATURE_WORDS = 4


@dataclasses.dataclass(frozen=True)
class Token:
    """One token and its character offset in the text."""

    text: str
    start: int

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    @property
    def lower(self) -> str:
        return self.text.lower()


def tokenize(text: str) -> list[Token]:
    """Split ``text`` into tokens; only whitespace is dropped."""
    return [Token(match[0], match.start()) for match in _TOKEN.finditer(text)]


def is_ident_word(text: str) -> bool:
    """Whether ``text`` can be a navaid, fix, or name word: two or more capitals,
    not a reserved abbreviation such as ``VORTAC`` or ``MSL``."""
    return bool(_UPPERCASE_WORD.fullmatch(text)) and text not in _RESERVED_WORDS


def phrase_signature(phrase: str) -> str:
    """Airport-independent form of a phrase: numbers and idents abstracted."""
    phrase = re.sub(r"R-\d+", "R-<n>", phrase)
    phrase = re.sub(r"(?<![<\w])\d+(?:\.\d+)?", "<n>", phrase)
    phrase = re.sub(
        r"\b[A-Z]{2,}\b",
        lambda word: word[0] if word[0] in _RESERVED_WORDS else "<id>",
        phrase,
    )
    return re.sub(r"\s+", " ", phrase).lower()


class TokenStream:
    """A cursor over ``text``'s tokens, matched case-insensitively."""

    def __init__(self, text: str):
        self._text = text
        self._tokens = tokenize(text)
        self._index = 0

    def _token(self, offset: int = 0) -> Token | None:
        index = self._index + offset
        return self._tokens[index] if index < len(self._tokens) else None

    def _peek(self, *words: str, offset: int = 0) -> bool:
        return all(
            (token := self._token(offset + i)) is not None and token.lower == word
            for i, word in enumerate(words)
        )

    def _accept(self, *words: str) -> bool:
        if not self._peek(*words):
            return False
        self._index += len(words)
        return True

    def _accept_any(self, *words: str) -> bool:
        """Consume whichever one of ``words`` comes next."""
        return any(self._accept(word) for word in words)

    def _expect(self, *words: str) -> None:
        for word in words:
            if not self._accept(word):
                raise self._unmatched()

    def _next(self) -> Token:
        token = self._token()
        if token is None:
            raise self._unmatched()
        self._index += 1
        return token

    def _at_end(self) -> bool:
        return self._index >= len(self._tokens)

    def _expect_end(self) -> None:
        if not self._at_end():
            raise self._unmatched()

    def _position(self) -> int:
        token = self._token()
        return token.start if token else len(self._text)

    def _slice_from(self, start: int) -> str:
        return self._text[start : self._tokens[self._index - 1].end]

    def _error(self, signature: str) -> ParseError:
        return ParseError(signature, self._upcoming_phrase(), self._position())

    def _unmatched(self) -> ParseError:
        if self._at_end():
            last = self._tokens[-1].lower if self._tokens else ""
            return ParseError(
                f'unexpected end after "{last}"', self._text[-40:], len(self._text)
            )
        phrase = self._upcoming_phrase()
        return ParseError(
            f'unmatched phrase "{phrase_signature(phrase)}"', phrase, self._position()
        )

    def _upcoming_phrase(self) -> str:
        upcoming = self._tokens[self._index : self._index + _SIGNATURE_WORDS]
        return self._text[upcoming[0].start : upcoming[-1].end] if upcoming else ""

    def _number(self) -> float:
        token = self._token()
        if token is None or not NUMBER.fullmatch(token.text):
            raise self._unmatched()
        self._index += 1
        return float(token.text)

    def _integer(self) -> int:
        token = self._token()
        if token is None or not token.text.isdigit():
            raise self._unmatched()
        self._index += 1
        return int(token.text)

    def _peek_integer(self, offset: int = 0) -> bool:
        token = self._token(offset)
        return token is not None and token.text.isdigit()
