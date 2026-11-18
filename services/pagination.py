"""One pagination helper for every list screen.

Page size is capped so a hand-edited query string cannot ask the server for
every row, and the result object carries what the template needs to draw
Previous/Next without recomputing anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


@dataclass(frozen=True)
class Page:
    items: list
    page: int
    page_size: int
    total: int
    pages: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def previous_page(self) -> int:
        return max(self.page - 1, 1)

    @property
    def next_page(self) -> int:
        return min(self.page + 1, self.pages)

    @property
    def start_index(self) -> int:
        return 0 if not self.items else (self.page - 1) * self.page_size + 1

    @property
    def end_index(self) -> int:
        return (self.page - 1) * self.page_size + len(self.items)

    @property
    def summary(self) -> str:
        if not self.total:
            return "No results"
        return f"{self.start_index}–{self.end_index} of {self.total}"


def clamp_page(page: int | str | None) -> int:
    try:
        value = int(page)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return max(value, 1)


def clamp_page_size(size: int | str | None, default: int = DEFAULT_PAGE_SIZE) -> int:
    try:
        value = int(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_PAGE_SIZE))


def paginate(
    items: Sequence,
    *,
    page: int | str | None = 1,
    page_size: int | str | None = DEFAULT_PAGE_SIZE,
) -> Page:
    size = clamp_page_size(page_size)
    total = len(items)
    pages = max((total + size - 1) // size, 1)
    current = min(clamp_page(page), pages)
    start = (current - 1) * size
    return Page(
        items=list(items[start : start + size]),
        page=current,
        page_size=size,
        total=total,
        pages=pages,
    )
