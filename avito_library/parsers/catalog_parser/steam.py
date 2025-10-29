"""Оркестратор для повторных запусков парсера каталога Авито."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable, Optional

from playwright.async_api import Page

from .catalog_parser import parse_catalog
from .models import (
    CatalogListing,
    CatalogParseMeta,
    CatalogParseResult,
    CatalogParseStatus,
)

# TODO(phase-2): вынести значения в конфигурацию катaлога.
MAX_PAGE_RETRIES = 5
BATCH_PAGE_LIMIT = 5

RECOVERABLE_STATUSES = {
    CatalogParseStatus.CAPTCHA_UNSOLVED,
    CatalogParseStatus.RATE_LIMIT,
    CatalogParseStatus.PROXY_BLOCKED,
    CatalogParseStatus.PROXY_AUTH_REQUIRED,
    CatalogParseStatus.LOAD_FAILED,
    CatalogParseStatus.INVALID_STATE,
}


@dataclass(slots=True)
class PageRequest:
    """Описание запроса на новую страницу от оркестратора."""

    attempt: int
    status: CatalogParseStatus
    details: Optional[str]
    next_start_page: int


class _PageExchange:
    """Простейший обмен сообщениями для запроса новых страниц."""

    def __init__(self) -> None:
        self._requests: asyncio.Queue[PageRequest] = asyncio.Queue()
        self._responses: asyncio.Queue[Page] = asyncio.Queue()

    async def request_page(self, payload: PageRequest) -> Page:
        """Отправляет запрос на новую страницу и ждёт ответа."""

        # TODO(phase-2): добавить таймаут и обработку отмены.
        await self._requests.put(payload)
        return await self._responses.get()

    async def next_request(self) -> PageRequest:
        """Возвращает детали ближайшего запроса на страницу."""

        return await self._requests.get()

    def supply_page(self, page: Page) -> None:
        """Передаёт новую страницу в ответ на ранее созданный запрос."""

        self._responses.put_nowait(page)


_EXCHANGE = _PageExchange()

__all__ = [
    "parse_catalog_until_complete",
    "PageRequest",
    "wait_for_page_request",
    "supply_page",
    "set_page_exchange",
]


def set_page_exchange(exchange: _PageExchange) -> None:
    """Позволяет подменить обмен страницами (удобно для тестов)."""

    global _EXCHANGE
    _EXCHANGE = exchange


async def wait_for_page_request() -> PageRequest:
    """Ожидает запрос на новую страницу от оркестратора."""

    return await _EXCHANGE.next_request()


def supply_page(page: Page) -> None:
    """Отдаёт новую страницу оркестратору."""

    _EXCHANGE.supply_page(page)


async def parse_catalog_until_complete(
    page: Page,
    catalog_url: str,
    *,
    fields: Iterable[str],
    max_pages: int | None = 1,
    sort_by_date: bool = False,
    include_html: bool = False,
    start_page: int = 1,
) -> CatalogParseResult:
    """Пытается собрать каталог целиком, запрашивая новые страницы по необходимости."""

    listings_acc: list[CatalogListing] = []
    total_processed_pages = 0
    total_processed_cards = 0
    attempt = 0
    current_page = page
    next_start_page = start_page
    last_meta: CatalogParseMeta | None = None

    while True:
        if max_pages is not None and total_processed_pages >= max_pages:
            break

        remaining = None
        if max_pages is not None:
            remaining = max_pages - total_processed_pages
            if remaining <= 0:
                break

        chunk_limit = None
        if remaining is None:
            chunk_limit = BATCH_PAGE_LIMIT
        else:
            chunk_limit = min(remaining, BATCH_PAGE_LIMIT)

        # Набор каждой итерации покрывает ограниченный набор страниц каталога.
        listings, meta = await parse_catalog(
            current_page,
            catalog_url,
            fields=fields,
            max_pages=chunk_limit,
            sort_by_date=sort_by_date,
            include_html=include_html,
            start_page=next_start_page,
        )

        listings_acc.extend(listings)
        total_processed_pages += meta.processed_pages
        total_processed_cards += meta.processed_cards
        last_meta = meta

        if meta.status is CatalogParseStatus.SUCCESS:
            return _compose_meta_result(
                listings_acc,
                total_processed_pages,
                total_processed_cards,
                meta,
                completed=True,
            )

        if max_pages is not None:
            remaining = max_pages - total_processed_pages
            if remaining <= 0:
                return _compose_meta_result(
                    listings_acc,
                    total_processed_pages,
                    total_processed_cards,
                    meta,
                    completed=meta.status is CatalogParseStatus.SUCCESS,
                )

        if meta.status not in RECOVERABLE_STATUSES:
            return _compose_meta_result(
                listings_acc,
                total_processed_pages,
                total_processed_cards,
                meta,
                completed=False,
            )

        attempt += 1
        if attempt >= MAX_PAGE_RETRIES:
            return _compose_meta_result(
                listings_acc,
                total_processed_pages,
                total_processed_cards,
                meta,
                completed=False,
                attempts_exhausted=True,
            )

        next_start_page = start_page + total_processed_pages

        request_payload = PageRequest(
            attempt=attempt,
            status=meta.status,
            details=meta.details,
            next_start_page=next_start_page,
        )
        # Ждём, пока внешний координатор выдаст свежую страницу Playwright.
        current_page = await _EXCHANGE.request_page(request_payload)

    if last_meta is None:
        empty_meta = CatalogParseMeta(
            status=CatalogParseStatus.EMPTY,
            processed_pages=0,
            processed_cards=0,
            last_state=None,
            details="Каталог не содержит страниц для обработки.",
            last_url=catalog_url,
        )
        return [], empty_meta

    return _compose_meta_result(
        listings_acc,
        total_processed_pages,
        total_processed_cards,
        last_meta,
        completed=False,
    )


def _compose_meta_result(
    listings: list[CatalogListing],
    processed_pages: int,
    processed_cards: int,
    base_meta: CatalogParseMeta,
    *,
    completed: bool,
    attempts_exhausted: bool = False,
) -> CatalogParseResult:
    """Собирает итоговую метаинформацию с пометкой о полноте данных."""

    details_suffix: list[str] = []
    if base_meta.details:
        details_suffix.append(base_meta.details)

    if not completed:
        details_suffix.append("Сбор каталога завершён не полностью.")
        if attempts_exhausted:
            # TODO(phase-2): расширить отчёт по попыткам.
            details_suffix.append("Достигнут лимит запросов дополнительной страницы.")

    merged_details = " ".join(details_suffix) if details_suffix else None

    merged_meta = CatalogParseMeta(
        status=base_meta.status if not completed else CatalogParseStatus.SUCCESS,
        processed_pages=processed_pages,
        processed_cards=processed_cards,
        last_state=base_meta.last_state,
        details=merged_details,
        last_url=base_meta.last_url,
    )
    # Комбинированный результат возвращается в исходном формате API.
    return listings, merged_meta