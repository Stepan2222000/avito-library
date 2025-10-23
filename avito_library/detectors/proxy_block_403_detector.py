"""Detector for proxy IP block (HTTP 403) pages."""

from __future__ import annotations

from typing import Final, Optional

from playwright.async_api import Error as PlaywrightError, Page, Response

__all__ = [
    "DETECTOR_ID",
    "CARD_SELECTOR",
    "BLOCK_PHRASES",
    "proxy_block_403_detector",
]

DETECTOR_ID: Final[str] = "proxy_block_403_detector"
CARD_SELECTOR: Final[str] = 'span[data-marker="item-view/item-id"]'
BLOCK_PHRASES: Final[tuple[str, ...]] = (
    "доступ ограничен:",
    "проблема с ip",
    "подождите немного и обновите страницу.",
    "если проблема не уходит, вот что можно сделать:",
    "отключить vpn.",
    "включить и выключить режим “в самолёте”.",
    "подключиться к другой сети.",
    "перезагрузить роутер.",
    "если и это не сработает,",
    "напишите в поддержку.",
    "в письме укажите город, провайдера и ip-адрес",
    "(его можно посмотреть на yandex.ru/internet).",
    "постараемся разобраться как можно скорее.",
)


async def proxy_block_403_detector(
    page: Page,
    *,
    last_response: Optional[Response] = None,
) -> str | bool:
    """Returns `proxy_block_403_detector` when the unblock instructions are present."""


    status = getattr(last_response, "status", None)
    if status == 403:
        return DETECTOR_ID

    if await _has_selector(page, CARD_SELECTOR):
        return False

    html = await _safe_page_content(page)
    if html is None:
        return False

    normalized = html.lower()
    phrases_present = all(phrase in normalized for phrase in BLOCK_PHRASES)
    if not phrases_present:
        return False

    # Даже при ответах 200/206 страница содержит тот же текст, поэтому считаем
    # прокси заблокированным, как только набор фраз совпадает полностью.
    return DETECTOR_ID


async def _has_selector(page: Page, selector: str) -> bool:
    try:
        return await page.locator(selector).count() > 0
    except PlaywrightError:
        return False


async def _safe_page_content(page: Page) -> Optional[str]:
    try:
        return await page.content()
    except PlaywrightError:
        return None
