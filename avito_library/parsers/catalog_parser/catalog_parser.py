"""Асинхронный парсер каталога Авито согласно README."""

from __future__ import annotations

import asyncio
from typing import Iterable

from playwright.async_api import Page, TimeoutError

from ...capcha.resolver import resolve_captcha_flow
from ...detectors import (
    CATALOG_DETECTOR_ID,
    CAPTCHA_DETECTOR_ID,
    PROXY_AUTH_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    DetectionError,
    detect_page_state,
)
from ...utils import press_continue_and_detect
from .helpers import (
    apply_sort,
    apply_start_page,
    extract_listing,
    get_next_page_url,
    has_empty_markers,
    load_catalog_cards,
)
from .models import (
    CatalogListing,
    CatalogParseMeta,
    CatalogParseResult,
    CatalogParseStatus,
)

LOAD_TIMEOUT = 60_000


async def parse_catalog(
    page: Page,
    catalog_url: str,
    *,
    fields: Iterable[str],
    max_pages: int | None = 1,
    sort_by_date: bool = False,
    include_html: bool = False,
    start_page: int = 1,
) -> CatalogParseResult:
    """Загружает страницы каталога Авито и извлекает указанные поля.

    Args:
        page: долговечная страница Playwright.
        catalog_url: исходный URL каталога.
        fields: набор идентификаторов полей (см. README).
        max_pages: максимальное количество страниц для обхода (>=1) или None для полного обхода.
        sort_by_date: включить ли сортировку `s=104`.
        include_html: сохранить ли HTML карточек в `raw_html`.
        start_page: номер страницы, с которой начинать обход.

    Returns:
        Кортеж из списка карточек и метаинформации выполнения.

    Raises:
        ValueError: при некорректных аргументах.
    """

    fields_set = set(fields)
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be >= 1 or None")
    if start_page < 1:
        raise ValueError("start_page must be >= 1")

    prepared_url = apply_sort(catalog_url, sort_by_date)
    # TODO(phase-2): предусмотреть проверку существования страницы перед переходом.
    prepared_url = apply_start_page(prepared_url, start_page)

    listings: list[CatalogListing] = []
    processed_pages = 0
    processed_cards = 0
    next_url: str | None = prepared_url
    current_url: str | None = None
    status = CatalogParseStatus.SUCCESS
    last_state: str | None = None
    details: str | None = None

    while next_url and (max_pages is None or processed_pages < max_pages):
        current_url = next_url
        try:
            await page.goto(
                current_url,
                wait_until="domcontentloaded",
                timeout=LOAD_TIMEOUT,
            )

        except TimeoutError:
            status = CatalogParseStatus.LOAD_FAILED
            details = f"Timeout while loading {current_url}"
            break

        try:
            state = await press_continue_and_detect(page)
        except DetectionError:
            status = CatalogParseStatus.INVALID_STATE
            details = "Failed to detect page state after pressing continue."
            last_state = "detection_error"
            break
        if state in {CAPTCHA_DETECTOR_ID, PROXY_BLOCK_429_DETECTOR_ID}:
            try:
                state = await _attempt_captcha_resolution(page, initial_state=state)
            except DetectionError:
                status = CatalogParseStatus.INVALID_STATE
                details = "Failed to resolve captcha or detect state afterwards."
                last_state = "detection_error"
                break
            if state in {CAPTCHA_DETECTOR_ID, PROXY_BLOCK_429_DETECTOR_ID}:
                status = (
                    CatalogParseStatus.CAPTCHA_UNSOLVED
                    if state == CAPTCHA_DETECTOR_ID
                    else CatalogParseStatus.RATE_LIMIT
                )
                details = "Failed to resolve captcha or persistent 429 after retries."
                last_state = state
                break
        else:
            try:
                state = await detect_page_state(page)
            except DetectionError:
                status = CatalogParseStatus.INVALID_STATE
                details = "No detector matched current page state."
                last_state = "detection_error"
                break

        last_state = state
        if state == CATALOG_DETECTOR_ID:
            processed_pages += 1
            cards = await load_catalog_cards(page)
            if not cards:
                page_html = await page.content()
                if has_empty_markers(page_html):
                    status = CatalogParseStatus.EMPTY
                    details = "Catalog page returned no items."
                    break
                status = CatalogParseStatus.INVALID_STATE
                details = "Catalog page detected but no cards were collected."
                break

            for card_locator in cards:
                listing = await extract_listing(
                    card_locator,
                    fields_set,
                    include_html=include_html,
                )
                if listing.item_id:
                    listings.append(listing)
            processed_cards += len(cards)

            has_next, candidate_url = await get_next_page_url(page, current_url)
            if not has_next:
                status = CatalogParseStatus.SUCCESS
                next_url = None
            else:
                next_url = candidate_url

            continue

        if state == PROXY_BLOCK_403_DETECTOR_ID:
            status = CatalogParseStatus.PROXY_BLOCKED
            details = "Received state proxy_block_403_detector."
            break

        if state == PROXY_AUTH_DETECTOR_ID:
            status = CatalogParseStatus.PROXY_AUTH_REQUIRED
            details = "Received state proxy_auth_407_detector."
            break

        if state == PROXY_BLOCK_429_DETECTOR_ID:
            status = CatalogParseStatus.RATE_LIMIT
            details = "Repeated 429 after captcha flow."
            break

        status = CatalogParseStatus.INVALID_STATE
        details = f"Unhandled state {state!r} on catalog page."
        break

    meta = CatalogParseMeta(
        status=status,
        processed_pages=processed_pages,
        processed_cards=processed_cards,
        last_state=last_state,
        details=details,
        last_url=current_url,
    )
    return listings, meta


async def _attempt_captcha_resolution(page: Page, *, initial_state: str) -> str:
    """Пробует решить капчу или 429 до пяти раз подряд."""

    max_attempts = 5
    attempts_left = max_attempts
    state = initial_state

    while attempts_left:
        _, solved = await resolve_captcha_flow(page)
        if solved:
            return await detect_page_state(page)
        attempts_left -= 1

    return state


__all__ = [
    "parse_catalog",
    "CatalogListing",
    "CatalogParseMeta",
    "CatalogParseStatus",
    "CatalogParseResult",
]
