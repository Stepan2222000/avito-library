"""Detector for removed or non-existent Avito listings."""

from __future__ import annotations

from typing import Final, Optional

from playwright.async_api import Error as PlaywrightError, Page, Response

from debug import DEBUG_SCREENSHOTS, capture_debug_screenshot

__all__ = [
    "DETECTOR_ID",
    "REMOVED_SELECTORS",
    "REMOVED_PHRASES",
    "removed_or_not_found_detector",
]

DETECTOR_ID: Final[str] = "removed_or_not_found_detector"
REMOVED_SELECTORS: Final[tuple[str, ...]] = (
    'div[data-marker="item-view/closed-warning"]',
    'div[data-marker="item-view/not-found"]',
)
REMOVED_PHRASES: Final[tuple[str, ...]] = (
    "такой страницы не существует",
    "объявление не посмотреть",
    "объявление снято с публикации",
    "объявление снято",
    "объявление находится на модерации",
    "объявление удалено",
    "объявление было удалено",
    "карточка недоступна",
    "объявление запрещено",
    "объявление скрыто владельцем",
)


async def removed_or_not_found_detector(
    page: Page,
    *,
    last_response: Optional[Response] = None,
) -> str | bool:
    """Detects pages where the listing is removed or missing."""

    status = getattr(last_response, "status", None)
    if status in {404, 410}:
        await capture_debug_screenshot(
            page,
            enabled=DEBUG_SCREENSHOTS,
            label=f"detector-removed-status-{status}",
        )
        return DETECTOR_ID

    if await _any_selector_present(page, REMOVED_SELECTORS):
        await capture_debug_screenshot(
            page,
            enabled=DEBUG_SCREENSHOTS,
            label="detector-removed-selector",
        )
        return DETECTOR_ID

    html = await _safe_page_content(page)
    if html is None:
        return False

    lowered = html.lower()
    for phrase in REMOVED_PHRASES:
        if phrase in lowered:
            await capture_debug_screenshot(
                page,
                enabled=DEBUG_SCREENSHOTS,
                label="detector-removed-phrase",
            )
            return DETECTOR_ID

    return False


async def _any_selector_present(page: Page, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        try:
            if await page.locator(selector).count() > 0:
                return True
        except PlaywrightError:
            continue
    return False


async def _safe_page_content(page: Page) -> Optional[str]:
    try:
        return await page.content()
    except PlaywrightError:
        return None
