"""Модели данных и статусы парсера каталога."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class CatalogParseStatus(Enum):
    """Статусы завершения парсинга каталога."""

    SUCCESS = "success"
    EMPTY = "empty"
    CAPTCHA_UNSOLVED = "captcha_unsolved"
    RATE_LIMIT = "rate_limit"
    PROXY_BLOCKED = "proxy_blocked"
    PROXY_AUTH_REQUIRED = "proxy_auth_required"
    INVALID_STATE = "invalid_state"
    LOAD_FAILED = "load_failed"


@dataclass(slots=True)
class CatalogListing:
    """Модель карточки с каталога Авито."""

    item_id: str
    title: str | None
    price: int | None
    snippet_text: str | None
    location_city: str | None
    location_area: str | None
    location_extra: str | None
    seller_name: str | None
    seller_id: str | None
    seller_rating: float | None
    seller_reviews: int | None
    promoted: bool
    published_ago: str | None
    raw_html: str | None


@dataclass(slots=True)
class CatalogParseMeta:
    """Метаинформация по итогам парсинга."""

    status: CatalogParseStatus
    processed_pages: int
    processed_cards: int
    last_state: str | None = None
    details: str | None = None
    last_url: str | None = None


CatalogParseResult = Tuple[list[CatalogListing], CatalogParseMeta]

__all__ = [
    "CatalogParseStatus",
    "CatalogListing",
    "CatalogParseMeta",
    "CatalogParseResult",
]
