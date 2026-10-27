"""Page-number pagination helpers used by the catalog API."""
from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class Page:
    items: list
    number: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, ceil(self.total / self.per_page))

    @property
    def has_next(self) -> bool:
        return self.number < self.pages

    @property
    def has_prev(self) -> bool:
        return self.number > 1


def paginate(items, page=1, per_page=20):
    """Return the 1-indexed ``page`` of ``items``."""
    if per_page < 1:
        raise ValueError("per_page must be positive")
    if page < 1:
        raise ValueError("page numbers start at 1")
    start = page * per_page
    end = start + per_page
    return Page(items=list(items[start:end]), number=page, per_page=per_page, total=len(items))
