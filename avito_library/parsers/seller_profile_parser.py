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

from ..debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

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
    include_items: bool = False,
    item_fields: Sequence[str] | None = None,
    item_schema: dict[str, Any] | None = None,
) -> SellerProfileParsingResult:
    """Collect seller name, item identifiers and optional payload from an opened Playwright page."""

    pages_collected = 0
    item_ids: list[int] = []
    allowed_conditions = _normalize_condition_titles(condition_titles)
    collect_details = include_items or bool(item_fields) or bool(item_schema)
    normalized_fields = (
        _normalize_item_fields(item_fields) if collect_details else None
    )
    item_details: list[dict[str, Any]] | None = [] if collect_details else None
    schema_by_id: dict[int, Any] | None = {} if item_schema else None

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

    await capture_debug_screenshot(
        page,
        enabled=DEBUG_SCREENSHOTS,
        label=f"seller-state-initial-{state}",
    )

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
        await capture_debug_screenshot(
            page,
            enabled=DEBUG_SCREENSHOTS,
            label=f"seller-state-after-captcha-{state}",
        )
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
            await capture_debug_screenshot(
                page,
                enabled=DEBUG_SCREENSHOTS,
                label=f"seller-unexpected-{state}",
            )
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
        await capture_debug_screenshot(
            page,
            enabled=DEBUG_SCREENSHOTS,
            label="seller-id-not-found",
        )
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
            include_details=collect_details,
            allowed_fields=normalized_fields,
            collect_schema=bool(item_schema),
        )
        if result is None:
            pagination_truncated = True
            break

        end, ids, details, raw_items = result
        pages_collected += 1

        if ids:
            item_ids.extend(ids)
        if collect_details and item_details is not None and details:
            item_details.extend(details)
        if schema_by_id is not None and raw_items:
            for item_id, raw_item in zip(ids, raw_items):
                schema_by_id[item_id] = _extract_from_schema(raw_item, item_schema or {})

        if end:
            pagination_complete = True
            break
        if MAX_PAGE and catalog_page >= MAX_PAGE:
            pagination_truncated = True
            break

    response: SellerProfileParsingResult = {
        "state": SELLER_PROFILE_DETECTOR_ID,
        "seller_name": seller_name,
        "item_ids": item_ids,
        "pages_collected": pages_collected,
        "is_complete": pagination_complete and not pagination_truncated,
    }
    if (include_items or bool(item_fields)) and item_details is not None:
        response["items"] = item_details
        response["item_titles"] = [
            item.get("title")
            for item in item_details
            if isinstance(item, dict) and isinstance(item.get("title"), str)
        ]
    if schema_by_id is not None:
        response["items_by_id"] = schema_by_id
    await capture_debug_screenshot(
        page,
        enabled=DEBUG_SCREENSHOTS,
        label="seller-finish",
    )
    return response


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
    *,
    include_details: bool,
    allowed_fields: set[str] | None,
    collect_schema: bool,
) -> tuple[
    bool,
    list[int],
    list[dict[str, Any]] | None,
    list[dict[str, Any]] | None,
] | None:
    url = (
        "https://www.avito.ru/web/1/profile/items"
        f"?sellerId={seller_id}&limit=100&p={items_page}"
    )
    logger.info("Fetching catalog page %s for seller %s", items_page, seller_id)

    payload = await _fetch_profile_items(page, url)

    catalog = payload.get("catalog")
    assert catalog, "Продавец не найден"

    items = catalog.get("items") or []
    filtered_ids: list[int] = []
    filtered_items: list[dict[str, Any]] = []
    schema_items: list[dict[str, Any]] | None = [] if collect_schema else None

    for item in items:
        raw_id = item.get("id")
        if raw_id is None:
            continue
        if not _passes_min_price(item, min_price):
            continue
        if not _matches_condition(item, allowed_conditions):
            continue

        item_id = _safe_int(raw_id)
        if item_id is None:
            continue

        filtered_ids.append(item_id)
        if include_details:
            filtered_items.append(_select_item_fields(item, allowed_fields))
        if schema_items is not None:
            schema_items.append(item)

    # Останавливаемся только когда API вернуло пустую страницу.
    return (
        not items,
        filtered_ids,
        filtered_items if include_details else None,
        schema_items,
    )


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


def _normalize_item_fields(item_fields: Sequence[str] | None) -> set[str] | None:
    if not item_fields:
        return None
    normalized = {
        field.strip()
        for field in item_fields
        if isinstance(field, str) and field.strip()
    }
    return normalized or None


def _select_item_fields(
    item: dict[str, Any],
    allowed_fields: set[str] | None,
) -> dict[str, Any]:
    if not allowed_fields:
        return item
    return {field: item.get(field) for field in allowed_fields}


def _extract_from_schema(
    item: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Build a payload for a single item according to the requested schema."""

    extracted: dict[str, Any] = {}
    for target_key, spec in schema.items():
        extracted[target_key] = _resolve_schema_spec(item, spec)
    return extracted


def _resolve_schema_spec(source: dict[str, Any], spec: Any) -> Any:
    if isinstance(spec, str):
        return _resolve_path(source, spec)
    if isinstance(spec, dict):
        return {
            key: _resolve_schema_spec(source, nested)
            for key, nested in spec.items()
        }
    return spec


def _resolve_path(source: Any, path: str) -> Any:
    if not path:
        return source
    parts = path.split(".")
    return _walk_path(source, parts)


def _walk_path(current: Any, parts: list[str]) -> Any:
    if not parts:
        return current

    segment = parts[0]
    is_list = segment.endswith("[]")
    key = segment[:-2] if is_list else segment

    next_value = _get_value(current, key)
    if next_value is None:
        return [] if is_list else None

    if is_list:
        if not isinstance(next_value, list):
            return []
        remainder = parts[1:]
        collected: list[Any] = []
        for entry in next_value:
            value = _walk_path(entry, remainder)
            if value is None:
                continue
            if isinstance(value, list):
                collected.extend(value)
            else:
                collected.append(value)
        return collected

    return _walk_path(next_value, parts[1:])


def _get_value(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return None


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
