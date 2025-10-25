"""Вспомогательные функции для парсинга каталога Авито."""

from __future__ import annotations

import re
from typing import Tuple
from urllib.parse import parse_qsl, urljoin, urlencode, urlparse, urlunparse

from playwright.async_api import Locator, Page, TimeoutError

from ...detectors.catalog_page_detector import CATALOG_ITEM_SELECTOR
from .models import CatalogListing

CATALOG_CARD_SELECTOR = CATALOG_ITEM_SELECTOR
NEXT_PAGE_SELECTOR = 'a[data-marker="pagination-button/nextPage"]'
SCROLL_ATTEMPTS = 10
SCROLL_DELAY_MS = 500
NETWORK_IDLE_TIMEOUT = 5_000
NETWORK_IDLE_FALLBACK_MS = 2_000
PROMOTED_BADGE_SELECTOR = '[data-marker^="badge-title"]'
SNIPPET_SELECTOR = 'div[class*="item-bottomBlock"] p'
SELLER_CONTAINER_SELECTOR = "div.iva-item-sellerInfo-w2qER"

__all__ = [
    "apply_sort",
    "apply_start_page",
    "load_catalog_cards",
    "get_next_page_url",
    "has_empty_markers",
    "extract_listing",
]


def apply_sort(url: str, sort_by_date: bool) -> str:
    """Добавляет к URL параметр сортировки по дате."""

    if not sort_by_date:
        return url

    parsed = urlparse(url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_params["s"] = "104"
    new_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def apply_start_page(url: str, start_page: int) -> str:
    """Добавляет к URL параметр начальной страницы каталога.

    TODO(phase-2): подумать о поддержке специализированных параметров пагинации.
    """

    if start_page <= 1:
        return url

    parsed = urlparse(url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_params["p"] = str(start_page)
    new_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


async def load_catalog_cards(page: Page) -> list[Locator]:
    """Скроллит страницу и возвращает список локаторов карточек."""

    previous_count = -1
    attempts = 0
    catalog_locator = page.locator(CATALOG_CARD_SELECTOR)

    while attempts < SCROLL_ATTEMPTS:
        attempts += 1
        try:
            await page.wait_for_timeout(SCROLL_DELAY_MS)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT)
            except TimeoutError:
                await page.wait_for_timeout(NETWORK_IDLE_FALLBACK_MS)

            current_count = await catalog_locator.count()
            if current_count == previous_count:
                break
            previous_count = current_count
        except TimeoutError:
            break

    return await catalog_locator.all()


async def get_next_page_url(page: Page, current_url: str) -> Tuple[bool, str | None]:
    """Возвращает абсолютный URL следующей страницы, если она есть."""

    next_button = page.locator(NEXT_PAGE_SELECTOR)
    if not await next_button.count():
        return False, None

    href = await next_button.first.get_attribute("href")
    if not href:
        return False, None

    return True, urljoin(current_url, href)


def has_empty_markers(html: str) -> bool:
    """Проверяет HTML на признаки пустого каталога или блокировки."""

    lower = html.lower()
    return any(
        marker in lower
        for marker in (
            "ничего не найдено",
            "доступ ограничен",
            "ничего не найдено по вашему запросу",
        )
    )


async def extract_listing(
    card: Locator,
    fields: set[str],
    *,
    include_html: bool,
) -> CatalogListing:
    """Извлекает данные из карточки согласно запрошенным полям."""

    item_id = (await card.get_attribute("data-item-id")) or ""
    title_value: str | None = None
    price_value: int | None = None
    snippet_text: str | None = None
    location_city: str | None = None
    location_area: str | None = None
    location_extra: str | None = None
    seller_name: str | None = None
    seller_id: str | None = None
    seller_rating: float | None = None
    seller_reviews: int | None = None
    promoted = False
    published_ago: str | None = None
    raw_html: str | None = None

    if "title" in fields:
        title_value = await _get_inner_text(card, 'a[data-marker="item-title"]')

    if not item_id:
        fallback_id = await _get_inner_text(card, 'div[data-marker="item-line"]')
        if fallback_id:
            item_id = fallback_id

    if "price" in fields:
        price_text = await _get_inner_text(card, '[data-marker="item-price"]')
        if price_text:
            price_value = _parse_price(price_text)

    if "snippet" in fields:
        snippet_text = await _extract_snippet(card)

    if "location" in fields:
        location_city, location_area, location_extra = await _extract_location(card)

    if "published" in fields:
        published_ago = await _get_inner_text(card, '[data-marker="item-date"]')

    if {"seller_name", "seller_id", "seller_rating", "seller_reviews"} & fields:
        seller_name, seller_id, seller_rating, seller_reviews = await _fill_seller_info(
            card,
            fields,
        )

    if "promoted" in fields:
        promoted = await card.locator(PROMOTED_BADGE_SELECTOR).count() > 0

    if include_html:
        raw_html = await card.inner_html()

    return CatalogListing(
        item_id=item_id,
        title=title_value,
        price=price_value,
        snippet_text=snippet_text,
        location_city=location_city,
        location_area=location_area,
        location_extra=location_extra,
        seller_name=seller_name,
        seller_id=seller_id,
        seller_rating=seller_rating,
        seller_reviews=seller_reviews,
        promoted=promoted,
        published_ago=published_ago,
        raw_html=raw_html,
    )


async def _get_inner_text(card: Locator, selector: str) -> str | None:
    node = card.locator(selector).first
    if not await node.count():
        return None
    text = (await node.inner_text()).strip()
    return text or None


async def _extract_snippet(card: Locator) -> str | None:
    meta_node = card.locator('meta[itemprop="description"]').first
    if await meta_node.count():
        content = await meta_node.get_attribute("content")
        if content:
            return content.strip()

    text_node = card.locator(SNIPPET_SELECTOR).first
    if await text_node.count():
        return (await text_node.inner_text()).strip()

    fallback = card.locator("p").first
    if await fallback.count():
        return (await fallback.inner_text()).strip()

    return None


async def _extract_location(card: Locator) -> Tuple[str | None, str | None, str | None]:
    location_node = card.locator('div[data-marker="item-location"]').first
    if not await location_node.count():
        location_node = card.locator('span[class*="geo"]').first
        if not await location_node.count():
            return None, None, None

    text = (await location_node.inner_text()).strip()
    if not text:
        return None, None, None

    parts = [segment.strip() for segment in text.replace("\u00a0", " ").split(",")]
    city = parts[0] if parts else None
    area = parts[1] if len(parts) > 1 else None

    extra: list[str] = []
    if len(parts) > 2:
        extra.extend(parts[2:])
    else:
        extra_candidate = await location_node.get_attribute("title")
        if extra_candidate:
            extra.append(extra_candidate.strip())

    extra_text = ", ".join(extra) if extra else None
    return city or None, area or None, extra_text


async def _fill_seller_info(
    card: Locator,
    fields: set[str],
) -> Tuple[str | None, str | None, float | None, int | None]:
    name: str | None = None
    seller_id: str | None = None
    rating: float | None = None
    reviews: int | None = None

    profile_link = card.locator(
        f"{SELLER_CONTAINER_SELECTOR} a[href*='/brands/'], "
        f"{SELLER_CONTAINER_SELECTOR} a[href*='/user/']"
    ).first
    if not await profile_link.count():
        profile_link = card.locator("a[href*='/brands/'], a[href*='/user/']").first
    if await profile_link.count():
        if "seller_name" in fields:
            name_node = profile_link.locator("p").first
            if await name_node.count():
                name = (await name_node.inner_text()).strip()
            else:
                name_text = await profile_link.inner_text()
                if name_text:
                    name = name_text.strip().splitlines()[0]
        href = await profile_link.get_attribute("href")
        if href and "seller_id" in fields:
            seller_id = _extract_seller_id(href)
    else:
        if "seller_name" in fields:
            name_node = card.locator(f"{SELLER_CONTAINER_SELECTOR} p").first
            if await name_node.count():
                name = (await name_node.inner_text()).strip()

    if "seller_rating" in fields:
        rating_text = await _get_inner_text(card, '[data-marker="seller-info/score"]')
        if not rating_text:
            rating_text = await _get_inner_text(card, '[data-marker="seller-rating/score"]')
        if rating_text:
            rating = _parse_float(rating_text)

    if "seller_reviews" in fields:
        reviews_text = await _get_inner_text(card, '[data-marker="seller-info/summary"]')
        if reviews_text:
            reviews = _extract_int(reviews_text)

    return name, seller_id, rating, reviews


def _extract_seller_id(href: str) -> str | None:
    path = urlparse(href).path.rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return None
    last_segment = segments[-1]
    if len(segments) >= 2 and segments[-2] in {"brands", "user"}:
        return last_segment
    return last_segment


def _parse_price(price_text: str) -> int | None:
    cleaned = re.sub(r"\D+", "", price_text.replace("\u00a0", " "))
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    normalized = value.replace(",", ".").replace("\u00a0", "").strip()
    try:
        return float(normalized)
    except ValueError:
        return None


def _extract_int(value: str) -> int | None:
    match = re.search(r"\d+", value.replace("\u00a0", ""))
    if not match:
        return None
    try:
        return int(match.group())
    except ValueError:
        return None
