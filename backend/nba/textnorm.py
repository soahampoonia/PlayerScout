"""Search-key normalisation. The frontend mirrors this exactly (src/search.ts),
so a change here needs the same change there."""

import re
import unicodedata

# Letters that Unicode decomposition (NFD) does not reduce to a base letter.
_SPECIAL = str.maketrans(
    {"đ": "d", "ð": "d", "ø": "o", "ł": "l", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i", "þ": "th"}
)
_DROP = re.compile(r"[.'’`]")
_SPACES = re.compile(r"\s+")


def search_key(text: str) -> str:
    """Lowercase, strip accents and punctuation: "Nikola Jokić" -> "nikola jokic",
    "Shai Gilgeous-Alexander" -> "shai gilgeous alexander", "P.J. Tucker" -> "pj tucker"."""
    text = (text or "").lower().translate(_SPECIAL)
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _DROP.sub("", text.replace("-", " "))
    return _SPACES.sub(" ", text).strip()
