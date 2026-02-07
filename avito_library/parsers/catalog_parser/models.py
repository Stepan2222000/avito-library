"""Модели данных и статусы парсера каталога."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from playwright.async_api import Page
    from ...utils.image_downloader import ImageResult


class CatalogParseStatus(Enum):
    """Статусы завершения парсинга каталога."""

    # === v2 статусы ===
    SUCCESS = "success"
    EMPTY = "empty"
    PROXY_BLOCKED = "proxy_blocked"
    PROXY_AUTH_REQUIRED = "proxy_auth_required"
    PAGE_NOT_DETECTED = "page_not_detected"
    LOAD_TIMEOUT = "load_timeout"
    CAPTCHA_FAILED = "captcha_failed"
    WRONG_PAGE = "wrong_page"
    SERVER_UNAVAILABLE = "server_unavailable"

    # === Legacy статусы (для совместимости со старым catalog_parser.py) ===
    CAPTCHA_UNSOLVED = "captcha_unsolved"
    RATE_LIMIT = "rate_limit"
    NOT_DETECTED = "not_detected"
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
    # Поля для изображений (опциональные, требуют "images" в fields)
    images: list[bytes] | None = None
    images_urls: list[str] | None = None
    images_errors: list[str] | None = None
    images_results: list[ImageResult] | None = None


@dataclass(slots=True)
class CatalogParseMeta:
    """Метаинформация по итогам парсинга."""

    status: CatalogParseStatus
    processed_pages: int
    processed_cards: int
    last_state: str | None = None
    details: str | None = None
    last_url: str | None = None


@dataclass
class SinglePageResult:
    """Результат парсинга одной страницы каталога."""

    status: CatalogParseStatus
    cards: list[CatalogListing]
    has_next: bool
    next_url: str | None = None
    error_state: str | None = None
    error_url: str | None = None


@dataclass
class CatalogParseResult:
    """Результат парсинга каталога с возможностью продолжения.

    Публичные поля содержат данные парсинга и информацию для продолжения.
    Приватные поля (_*) используются методом continue_from() для возобновления.
    """

    # Публичные поля
    status: CatalogParseStatus
    listings: list[CatalogListing]
    meta: CatalogParseMeta
    error_state: str | None = None
    error_url: str | None = None
    resume_url: str | None = None
    resume_page_number: int | None = None

    # Приватные поля для continue_from (не показываются в repr)
    _catalog_url: str = field(default="", repr=False)
    _fields: set = field(default_factory=set, repr=False)
    _max_pages: int | None = field(default=None, repr=False)
    _sort: str | None = field(default=None, repr=False)
    _start_page: int = field(default=1, repr=False)
    _include_html: bool = field(default=False, repr=False)
    _max_captcha_attempts: int = field(default=30, repr=False)
    _load_timeout: int = field(default=180_000, repr=False)
    _load_retries: int = field(default=5, repr=False)
    _processed_pages: int = field(default=0, repr=False)
    _single_page: bool = field(default=False, repr=False)

    async def continue_from(
        self,
        new_page: "Page",
        skip_navigation: bool | None = None,
    ) -> "CatalogParseResult":
        """Продолжает парсинг каталога с новой страницей.

        Используется после критической ошибки (PROXY_BLOCKED, PROXY_AUTH_REQUIRED
        и т.д.) для возобновления парсинга с новым прокси/страницей.

        Args:
            new_page: Новая страница Playwright (обычно с другим прокси).
            skip_navigation: Управление навигацией:
                - True: не делать goto (страница уже открыта на нужном URL)
                - False: делать navigate_to_catalog на resume_url
                - None (по умолчанию): автоопределение через detect_page_state

        Returns:
            Новый CatalogParseResult с объединёнными данными.

        Raises:
            ValueError: Если результат получен в режиме single_page.
        """
        # Проверка режима single_page
        if self._single_page:
            raise ValueError(
                "Невозможно продолжить парсинг: результат получен в режиме single_page"
            )

        # Отложенный импорт для избежания циклической зависимости
        from .catalog_parser_v2 import _continue_parsing

        return await _continue_parsing(self, new_page, skip_navigation)


# Legacy TypeAlias для обратной совместимости со старым catalog_parser.py
CatalogParseResultLegacy = Tuple[list[CatalogListing], CatalogParseMeta]

__all__ = [
    "CatalogParseStatus",
    "CatalogListing",
    "CatalogParseMeta",
    "SinglePageResult",
    "CatalogParseResult",
    "CatalogParseResultLegacy",
]
