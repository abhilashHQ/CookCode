from collections.abc import Callable
from typing import Any, cast


def tag_or_fallback(known: frozenset[str], fallback: str) -> Callable[[Any], str]:
    def discriminate(value: Any) -> str:
        if isinstance(value, dict):
            tag = cast(dict[str, Any], value).get("type")
        else:
            tag = getattr(value, "type", None)
        return tag if isinstance(tag, str) and tag in known else fallback

    return discriminate
