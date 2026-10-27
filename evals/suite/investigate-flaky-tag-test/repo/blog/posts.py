from dataclasses import dataclass, field
from datetime import date

from .tags import merge_tags, parse_tags


@dataclass
class Post:
    title: str
    published: date
    tags: list = field(default_factory=list)
    extra_tags: str = ""  # free-form tags typed into the CMS sidebar

    @property
    def all_tags(self):
        return merge_tags(self.tags, parse_tags(self.extra_tags))

    @property
    def slug(self):
        return "-".join(self.title.lower().split())


def recent(posts, today, days=30):
    return sorted(
        (p for p in posts if 0 <= (today - p.published).days <= days),
        key=lambda p: p.published,
        reverse=True,
    )
