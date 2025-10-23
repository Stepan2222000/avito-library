"""Seller profile parser built on top of Playwright primitives.

The implementation mirrors the proven logic from
`plan/avito_library/scrape_seller_items/main.py`, but adapts it to work with an
already opened Playwright page. We keep the retry behaviour and pagination flow
from the legacy scraper while avoiding database writes and direct Redis/proxy
usage.
"""

from __future__ import annotations

import json
import logging
import re
from asyncio import sleep
from functools import wraps
from itertools import count
from typing import Any, Awaitable, Callable, Sequence

from playwright.async_api import Page

from ..config import MAX_PAGE

from ..capcha.resolver import resolve_captcha_flow
from ..detectors import (
    CAPTCHA_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    SELLER_PROFILE_DETECTOR_ID,
    detect_page_state,
)
from ..detectors.detect_page_state import DetectionError
from ..detectors.seller_profile_detector import NAME_SELECTOR

__all__ = ["collect_seller_items", "SellerProfileParsingResult", "SellerIdNotFound"]


logger = logging.getLogger(__name__)


class SellerIdNotFound(RuntimeError):
    """Raised when the seller identifier cannot be extracted from HTML."""


class CatalogRequestError(RuntimeError):
    """Raised when the profile/items endpoint returns an unexpected payload."""


SellerProfileParsingResult = dict[str, Any]


def retry(
    *exceptions: type[Exception],
    tries: int = 30,
    skip: type[Exception | tuple[Exception, ...]] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Asynchronous retry decorator copied from the legacy scraper."""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def __wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: BaseException | None = None
            for attempt in range(tries):
                try:
                    return await func(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - keep old behaviour
                    last_error = exc
                    if skip and isinstance(exc, skip):
                        logger.info("%s: %s", func.__name__, exc, exc_info=True)
                        break
                    if isinstance(exc, exceptions):
                        logger.warning(
                            "%s attempt %s failed: %s",
                            func.__name__,
                            attempt + 1,
                            exc,
                            exc_info=True,
                        )
                    else:
                        raise
                await sleep(10)
            else:
                if last_error is not None:
                    logger.error(
                        "Retry exhausted for %s due to %s",
                        func.__name__,
                        last_error,
                        exc_info=True,
                    )
            return None

        return __wrapper

    return decorator


async def collect_seller_items(
    page: Page,
    *,
    min_price: int | None = 8000,
    condition_titles: Sequence[str] | None = None,
) -> SellerProfileParsingResult:
    """Collect seller name and item identifiers from an opened Playwright page."""

    pages_collected = 0
    item_ids: list[int] = []
    allowed_conditions = _normalize_condition_titles(condition_titles)

    try:
        state = await detect_page_state(page, priority=[SELLER_PROFILE_DETECTOR_ID])
    except DetectionError as exc:
        if await _looks_like_profile(page):
            logger.debug("Fallback profile detection triggered after failure")
            state = SELLER_PROFILE_DETECTOR_ID
        else:
            logger.error("Seller profile detector failed: %s", exc)
            return {
                "state": "detection_error",
                "seller_name": None,
                "item_ids": item_ids,
                "pages_collected": pages_collected,
                "is_complete": False,
            }

    seller_html: str | None = None

    if state == SELLER_PROFILE_DETECTOR_ID:
        seller_html = await page.content()
    elif state in {CAPTCHA_DETECTOR_ID, PROXY_BLOCK_429_DETECTOR_ID}:
        seller_html, _ = await resolve_captcha_flow(page)
        try:
            state = await detect_page_state(page, priority=[SELLER_PROFILE_DETECTOR_ID])
        except DetectionError as exc:
            logger.error("Seller profile detector failed after captcha: %s", exc)
            return {
                "state": "detection_error",
                "seller_name": None,
                "item_ids": item_ids,
                "pages_collected": pages_collected,
                "is_complete": False,
            }
        if state != SELLER_PROFILE_DETECTOR_ID:
            return {
                "state": state,
                "seller_name": None,
                "item_ids": item_ids,
                "pages_collected": pages_collected,
                "is_complete": False,
            }
        seller_html = await page.content()
    else:
        if await _looks_like_profile(page):
            logger.debug("Fallback profile detection triggered for state %s", state)
            seller_html = await page.content()
            state = SELLER_PROFILE_DETECTOR_ID
        else:
            # Передаём внешний детектор как признак того, что собрать данные не удалось.
            return {
                "state": state,
                "seller_name": None,
                "item_ids": item_ids,
                "pages_collected": pages_collected,
                "is_complete": False,
            }

    seller_name = await _extract_seller_name(page)
    try:
        seller_id = _extract_seller_id(seller_html)
    except SellerIdNotFound:
        return {
            "state": "seller_id_not_found",
            "seller_name": seller_name,
            "item_ids": item_ids,
            "pages_collected": pages_collected,
            "is_complete": False,
        }

    pagination_complete = False
    pagination_truncated = False

    for catalog_page in count(1):
        result = await _catalog(
            page,
            seller_id,
            catalog_page,
            min_price,
            allowed_conditions,
        )
        if result is None:
            pagination_truncated = True
            break

        end, ids = result
        pages_collected += 1

        if ids:
            item_ids.extend(ids)

        if end:
            pagination_complete = True
            break
        if MAX_PAGE and catalog_page >= MAX_PAGE:
            pagination_truncated = True
            break

    return {
        "state": SELLER_PROFILE_DETECTOR_ID,
        "seller_name": seller_name,
        "item_ids": item_ids,
        "pages_collected": pages_collected,
        "is_complete": pagination_complete and not pagination_truncated,
    }


async def _extract_seller_name(page: Page) -> str | None:
    locator = page.locator(NAME_SELECTOR).first
    try:
        raw = await locator.text_content()
    except Exception:  # noqa: BLE001 - mirrors previous tolerant behaviour
        return None
    if not raw:
        return None
    name = raw.strip()
    return name or None


def _extract_seller_id(html: str) -> str:
    match = re.search(r"sellerId=([0-9a-f]+)", html)
    if not match:
        raise SellerIdNotFound("Seller identifier not found in HTML")
    return match.group(1)


@retry(Exception, skip=AssertionError)
async def _catalog(
    page: Page,
    seller_id: str,
    items_page: int,
    min_price: int | None,
    allowed_conditions: set[str] | None,
) -> tuple[bool, list[int]] | None:
    url = (
        "https://www.avito.ru/web/1/profile/items"
        f"?sellerId={seller_id}&limit=100&p={items_page}"
    )
    logger.info("Fetching catalog page %s for seller %s", items_page, seller_id)

    payload = await _fetch_profile_items(page, url)

    catalog = payload.get("catalog")
    assert catalog, "Продавец не найден"

    items = catalog.get("items") or []
    ids = [
        _safe_int(item.get("id"))
        for item in items
        if item.get("id") is not None
        and _passes_min_price(item, min_price)
        and _matches_condition(item, allowed_conditions)
    ]
    ids = [item_id for item_id in ids if item_id is not None]

    # Останавливаемся только когда API вернуло пустую страницу.
    return (not items, ids)


async def _fetch_profile_items(page: Page, url: str) -> dict[str, Any]:
    result = await page.evaluate(
        """async (target) => {
            const response = await fetch(target, { credentials: 'include' });
            const text = await response.text();
            return { status: response.status, body: text };
        }""",
        url,
    )

    status = result.get("status")
    if status != 200:
        logger.error("Catalog request failed with status %s", status)
        raise CatalogRequestError(f"Unexpected status code: {status}")

    body = result.get("body", "")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:  # noqa: F841 - used for logging below
        logger.exception("Failed to decode catalog JSON")
        raise CatalogRequestError("Invalid JSON payload") from exc


def _matches_condition(item: dict[str, Any], allowed_conditions: set[str] | None) -> bool:
    if not allowed_conditions:
        return True
    titles = _extract_badge_titles(item)
    if not titles:
        return False
    return any(title in allowed_conditions for title in titles)


def _extract_badge_titles(item: dict[str, Any]) -> set[str]:
    titles: set[str] = set()
    badges = item.get("badges")
    if isinstance(badges, list):
        titles.update(_collect_badge_titles(badges))

    iva = item.get("iva")
    if isinstance(iva, dict):
        titles.update(_collect_badge_titles(iva))

    return {title.lower() for title in titles}


def _collect_badge_titles(source: Any) -> list[str]:
    titles: list[str] = []
    if isinstance(source, list):
        for entry in source:
            titles.extend(_collect_badge_titles(entry))
    elif isinstance(source, dict):
        badges = source.get("badges")
        if isinstance(badges, list):
            for badge in badges:
                title = badge.get("title")
                if isinstance(title, str):
                    titles.append(title)
        for key, value in source.items():
            if key == "badges":
                continue
            titles.extend(_collect_badge_titles(value))
    return titles


def _normalize_condition_titles(condition_titles: Sequence[str] | None) -> set[str] | None:
    if not condition_titles:
        return None
    normalized = {
        title.strip().lower()
        for title in condition_titles
        if isinstance(title, str) and title.strip()
    }
    return normalized or None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _looks_like_profile(page: Page) -> bool:
    try:
        profile_count = await page.locator('div[data-marker="profile"]').count()
        name_count = await page.locator('h1[data-marker^="name "]').count()
    except Exception:
        return False
    return profile_count > 0 and name_count > 0


def _passes_min_price(item: dict[str, Any], min_price: int | None) -> bool:
    if min_price is None:
        return True

    price_info = item.get("priceDetailed")
    if not isinstance(price_info, dict):
        return False

    if not price_info.get("enabled") or not price_info.get("hasValue"):
        return False

    value = price_info.get("value")
    if isinstance(value, (int, float)):
        return value >= min_price

    try:
        return int(value) >= min_price
    except (TypeError, ValueError):
        return False
