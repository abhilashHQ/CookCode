"""String helpers shared by the blog engine."""
import re
import unicodedata

_NON_WORD = re.compile(r"[^\w\s-]")
_SEPARATORS = re.compile(r"[-\s_]+")


def slugify(text, separator="-"):
    """Turn ``text`` into a lowercase ASCII slug.

    >>> slugify("Hello, World!")
    'hello-world'
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = _NON_WORD.sub("", text).strip().lower()
    return _SEPARATORS.sub(separator, text).strip(separator)


def excerpt(text, limit=160, suffix="..."):
    """Shorten ``text`` to ``limit`` characters on a word boundary."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - len(suffix)]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + suffix


def reading_time(text, wpm=230):
    words = len(text.split())
    return max(1, round(words / wpm))
