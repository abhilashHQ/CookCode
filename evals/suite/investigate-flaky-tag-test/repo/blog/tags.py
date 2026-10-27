import re
from collections import Counter

_SPLIT = re.compile(r"[,\s]+")


def normalize(tag):
    return tag.strip().lower().lstrip("#")


def parse_tags(raw):
    """Parse a free-form tag string like "#Python, testing ci"."""
    return [t for t in (normalize(part) for part in _SPLIT.split(raw)) if t]


def merge_tags(*groups):
    """Combine tag lists from several sources, dropping duplicates."""
    seen = set()
    for group in groups:
        seen.update(normalize(t) for t in group)
    seen.discard("")
    return list(seen)


def tag_cloud(posts, limit=10):
    counts = Counter(tag for post in posts for tag in post.all_tags)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
