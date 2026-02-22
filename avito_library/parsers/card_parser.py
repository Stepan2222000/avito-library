"""Card parser draft implementation for Avito library plan."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup
from playwright.async_api import Page, Response

from avito_library.detectors import (
    detect_page_state,
    CAPTCHA_DETECTOR_ID,
    CARD_FOUND_DETECTOR_ID,
    NOT_DETECTED_STATE_ID,
    PROXY_AUTH_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
    SERVER_ERROR_5XX_DETECTOR_ID,
    UNKNOWN_PAGE_DETECTOR_ID,
)
from avito_library.capcha import resolve_captcha_flow
from avito_library.utils.image_downloader import ImageResult, download_images as _download_images

logger = logging.getLogger(__name__)

__all__ = ["CardData", "CardParsingError", "parse_card", "CardParseStatus", "CardParseResult"]


class CardParsingError(RuntimeError):
    """Raised when HTML does not correspond to an Avito card."""


@dataclass(slots=True)
class CardData:
    title: Optional[str] = None
    price: Optional[int] = None
    seller: Optional[dict[str, Optional[str]]] = None
    item_id: Optional[int] = None
    published_at: Optional[str] = None
    description: Optional[str] = None
    location: Optional[dict[str, Optional[str]]] = None
    characteristics: Optional[dict[str, str]] = None
    views_total: Optional[int] = None
    images: Optional[list[bytes]] = None
    images_urls: Optional[list[str]] = None
    images_errors: Optional[list[str]] = None
    images_results: Optional[list[ImageResult]] = None
    raw_html: Optional[str] = None


class CardParseStatus(Enum):
    """Статус результата парсинга карточки."""
    SUCCESS = "success"
    CAPTCHA_FAILED = "captcha_failed"
    PROXY_BLOCKED = "proxy_blocked"
    NOT_FOUND = "not_found"
    PAGE_NOT_DETECTED = "page_not_detected"
    WRONG_PAGE = "wrong_page"
    SERVER_UNAVAILABLE = "server_unavailable"


@dataclass(slots=True)
class CardParseResult:
    """Результат парсинга карточки."""
    status: CardParseStatus
    data: Optional[CardData] = None


_SUPPORTED_FIELDS = {
    "title",
    "price",
    "seller",
    "item_id",
    "published_at",
    "description",
    "location",
    "characteristics",
    "views_total",
    "images",
    "raw_html",
}



async def _parse_card_html(
    html: str,
    *,
    page: Page,
    fields: Iterable[str],
    include_html: bool = False,
) -> CardData:
    """Парсит HTML карточки и возвращает CardData."""
    if not isinstance(html, str) or not html.strip():
        raise ValueError("html must be a non-empty string")

    requested_fields = {field for field in fields if field in _SUPPORTED_FIELDS}
    soup = BeautifulSoup(html, "lxml")

    data = CardData()

    if "title" in requested_fields:
        data.title = _extract_text(
            soup.select_one('h1[itemprop="name"]')
            or soup.select_one('h1[data-marker="item-view/title-info"]')
        )

    if "price" in requested_fields:
        data.price = _extract_price(soup)

    if "seller" in requested_fields:
        data.seller = _extract_seller(soup)

    if "item_id" in requested_fields or "published_at" in requested_fields:
        item_id, published_at = _extract_item_meta(soup)
        if "item_id" in requested_fields:
            data.item_id = item_id
        if "published_at" in requested_fields:
            data.published_at = published_at

    if "description" in requested_fields:
        data.description = _extract_description(soup)

    if "location" in requested_fields:
        data.location = _extract_location(soup)

    if "characteristics" in requested_fields:
        data.characteristics = _extract_characteristics(soup)

    if "views_total" in requested_fields:
        data.views_total = _extract_views(soup)

    if "images" in requested_fields:
        urls = await _extract_images(soup, html, page)
        data.images_urls = urls
        if urls:
            data.images_results = await _download_images(urls, page)
            data.images = [r.data for r in data.images_results if r.success and r.data]
            data.images_errors = [f"{r.url}: {r.error}" for r in data.images_results if not r.success]
        else:
            data.images_results = []
            data.images = []
            data.images_errors = []

    if include_html:
        data.raw_html = html
    elif "raw_html" in requested_fields:
        data.raw_html = html

    return data


# Состояния, при которых решаем капчу
_CAPTCHA_STATES = frozenset({
    CAPTCHA_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
})


async def parse_card(
    page: Page,
    last_response: Response,
    *,
    fields: Iterable[str],
    max_captcha_attempts: int = 30,
    include_html: bool = False,
) -> CardParseResult:
    """
    Парсит карточку объявления с автоматической обработкой состояний.

    Функция получает Playwright Page с уже открытой страницей карточки,
    автоматически детектирует состояние страницы, решает капчу при
    необходимости и возвращает результат со статусом.

    Args:
        page: Playwright Page с уже открытой страницей карточки.
        last_response: Response от навигации (goto).
        fields: Какие поля парсить (из CardData).
        max_captcha_attempts: Максимум попыток решения капчи (по умолчанию 30).
        include_html: Включить raw_html в результат.

    Returns:
        CardParseResult с полями:
        - status: CardParseStatus (SUCCESS, CAPTCHA_FAILED, PROXY_BLOCKED, NOT_FOUND, PAGE_NOT_DETECTED)
        - data: CardData при SUCCESS, None при ошибке
    """
    # 1. Детектируем начальное состояние
    state = await detect_page_state(page, last_response=last_response)

    # 2. Retry при серверных ошибках 5xx (502, 503, 504)
    if state == SERVER_ERROR_5XX_DETECTOR_ID:
        retry_delays = (2.0, 4.0, 8.0)  # Exponential backoff
        for delay in retry_delays:
            await asyncio.sleep(delay)
            reload_response = await page.reload()
            state = await detect_page_state(page, last_response=reload_response)
            if state != SERVER_ERROR_5XX_DETECTOR_ID:
                break
        else:
            # Все попытки исчерпаны — сервер недоступен
            return CardParseResult(status=CardParseStatus.SERVER_UNAVAILABLE)

    # 3. Цикл решения капчи если нужно
    captcha_attempts = 0
    while state in _CAPTCHA_STATES and captcha_attempts < max_captcha_attempts:
        captcha_attempts += 1
        _, solved = await resolve_captcha_flow(page, max_attempts=1)
        if solved:
            state = await detect_page_state(page)

            # Критические ошибки — сразу возвращаем результат
            if state in (PROXY_BLOCK_403_DETECTOR_ID, PROXY_AUTH_DETECTOR_ID):
                return CardParseResult(status=CardParseStatus.PROXY_BLOCKED)

            if state == REMOVED_DETECTOR_ID:
                return CardParseResult(status=CardParseStatus.NOT_FOUND)

            # Выход из цикла при любом не-капча состоянии
            if state not in _CAPTCHA_STATES:
                break

    # 4. Обработка финального состояния
    if state == CARD_FOUND_DETECTOR_ID:
        html = await page.content()
        data = await _parse_card_html(html, page=page, fields=fields, include_html=include_html)
        return CardParseResult(status=CardParseStatus.SUCCESS, data=data)

    if state in (PROXY_BLOCK_403_DETECTOR_ID, PROXY_AUTH_DETECTOR_ID):
        return CardParseResult(status=CardParseStatus.PROXY_BLOCKED)

    if state == REMOVED_DETECTOR_ID:
        return CardParseResult(status=CardParseStatus.NOT_FOUND)

    if state in _CAPTCHA_STATES:
        return CardParseResult(status=CardParseStatus.CAPTCHA_FAILED)

    if state == NOT_DETECTED_STATE_ID:
        return CardParseResult(status=CardParseStatus.PAGE_NOT_DETECTED)

    # Unknown page detector — известный edge case (журнал, и т.д.)
    if isinstance(state, str) and state.startswith(UNKNOWN_PAGE_DETECTOR_ID):
        return CardParseResult(status=CardParseStatus.WRONG_PAGE)

    # Неизвестный детектор (не должно произойти)
    return CardParseResult(status=CardParseStatus.PAGE_NOT_DETECTED)


def _extract_text(node) -> Optional[str]:
    if node is None:
        return None
    text = node.get_text(strip=True)
    return text or None


def _extract_price(soup: BeautifulSoup) -> Optional[int]:
    node = (
        soup.select_one('span[itemprop="price"][data-marker="item-view/item-price"]')
        or soup.select_one('meta[itemprop="price"]')
    )
    if node is None:
        return None
    value = node.get("content") or node.get("value") or node.get_text(strip=True)
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _extract_seller(soup: BeautifulSoup) -> Optional[dict[str, Optional[str]]]:
    name_node = soup.select_one('div[data-marker="seller-info/name"] span')
    link_node = (
        soup.select_one('a[data-marker="seller-link/link"]')
        or soup.select_one('a[data-marker="seller-info/profile-link"]')
    )
    name = _extract_text(name_node)
    profile_url = link_node.get("href") if link_node else None
    if name is None and profile_url is None:
        return None
    return {"name": name, "profile_url": profile_url}


def _extract_item_meta(soup: BeautifulSoup) -> tuple[Optional[int], Optional[str]]:
    id_node = soup.select_one('span[data-marker="item-view/item-id"]')
    date_node = soup.select_one('span[data-marker="item-view/item-date"]')

    item_id: Optional[int] = None
    if id_node is not None:
        digits = re.findall(r"\d+", id_node.get_text(separator=" ", strip=True))
        item_id = int(digits[0]) if digits else None

    published_at = _extract_text(date_node)
    if published_at is None and id_node is not None:
        raw = id_node.get_text(separator=" ", strip=True)
        if "·" in raw:
            parts = [part.strip() for part in raw.split("·", 1)]
            if len(parts) == 2:
                published_at = parts[1] or None
    if published_at and published_at.startswith("·"):
        published_at = published_at.lstrip("· ")

    return item_id, published_at


def _extract_description(soup: BeautifulSoup) -> Optional[str]:
    node = soup.select_one('div[data-marker="item-view/item-description"]') or soup.select_one(
        '#bx_item-description'
    )
    if node is None:
        return None
    text = node.get_text("\n", strip=True)
    return text or None


def _extract_location(soup: BeautifulSoup) -> Optional[dict[str, Optional[str]]]:
    container = soup.select_one('div[itemtype="http://schema.org/PostalAddress"]') or soup.select_one(
        'div[data-marker="item-view/item-location"]'
    )
    if container is None:
        return None

    address = None
    metro = None
    region = None

    for span in container.select("span"):
        text = span.get_text(" ", strip=True)
        if not text:
            continue
        marker = (span.get("data-marker") or "").lower()
        itemprop = (span.get("itemprop") or "").lower()
        if address is None and ("address" in marker or itemprop in {"address", "streetaddress", "addresslocality"}):
            address = text
        elif metro is None and ("metro" in marker or itemprop == "metrostation"):
            metro = text
        elif region is None and any(key in marker for key in ("region", "district", "area")):
            region = text

    if address is None:
        address = container.get_text(" ", strip=True) or None

    if address is None and metro is None and region is None:
        return None

    return {"address": address, "metro": metro, "region": region}


def _extract_characteristics(soup: BeautifulSoup) -> Optional[dict[str, str]]:
    items = soup.select('#bx_item-params li')
    if not items:
        items = soup.select('li[data-marker="item-parameters/list-item"]')
    characteristics: dict[str, str] = {}
    for item in items:
        parts = list(item.stripped_strings)
        if not parts:
            continue
        key = parts[0]
        value = " ".join(parts[1:]) if len(parts) > 1 else ""
        if value.startswith(":"):
            value = value.lstrip(": 	")
        if key:
            characteristics[key] = value
    return characteristics or None


def _extract_views(soup: BeautifulSoup) -> Optional[int]:
    node = soup.select_one('span[data-marker="item-view/total-views"]')
    if node is None:
        return None
    digits = re.findall(r"\d+", node.get_text())
    if not digits:
        return None
    try:
        return int("".join(digits))
    except ValueError:
        return None


# ============================================================================
# Image extraction (HIGH QUALITY 1280x960)
# ============================================================================

# Альтернативные пути к imageUrls (защита от изменений структуры Avito)
_IMAGE_PATHS: list[list[str]] = [
    # Новый формат (React Router, февраль 2026+)
    ["loaderData", "catalog-or-main-or-item", "buyerItem", "item", "imageUrls"],
    # Старый формат (__preloadedState__)
    ["@avito/bx-item-view", "buyerItem", "item", "imageUrls"],
    ["@avito/bx-item-view-v2", "buyerItem", "item", "imageUrls"],
    ["buyerItem", "item", "imageUrls"],
    ["item", "imageUrls"],
]

# Приоритет размеров — ТОЛЬКО высокое разрешение
_SIZE_PRIORITY: list[str] = ["1280x960", "640x480"]

# Максимальная глубина рекурсивного поиска
_MAX_RECURSION_DEPTH: int = 6

# JS-код для page.evaluate() — читает из обоих источников данных Avito
_JS_EXTRACT_IMAGE_URLS: str = """
(() => {
    const sizes = ['1280x960', '640x480'];

    const paths = [
        ['loaderData', 'catalog-or-main-or-item', 'buyerItem', 'item', 'imageUrls'],
        ['@avito/bx-item-view', 'buyerItem', 'item', 'imageUrls'],
        ['@avito/bx-item-view-v2', 'buyerItem', 'item', 'imageUrls'],
        ['buyerItem', 'item', 'imageUrls'],
        ['item', 'imageUrls'],
    ];

    function extractFrom(state) {
        if (!state || typeof state !== 'object') return null;
        for (const path of paths) {
            let cur = state;
            for (const k of path) {
                if (!cur || typeof cur !== 'object') { cur = null; break; }
                cur = cur[k];
            }
            if (Array.isArray(cur) && cur.length > 0) {
                const urls = cur.map(img => {
                    if (typeof img !== 'object' || img === null) return null;
                    for (const s of sizes) { if (img[s]) return img[s]; }
                    return null;
                }).filter(Boolean);
                if (urls.length > 0) return urls;
            }
        }
        function find(obj, depth) {
            if (depth > 6 || !obj || typeof obj !== 'object') return null;
            if (Array.isArray(obj)) {
                for (const item of obj) { const r = find(item, depth+1); if (r) return r; }
                return null;
            }
            if ('imageUrls' in obj && Array.isArray(obj.imageUrls) && obj.imageUrls.length > 0) {
                const urls = obj.imageUrls.map(img => {
                    if (typeof img !== 'object' || img === null) return null;
                    for (const s of sizes) { if (img[s]) return img[s]; }
                    return null;
                }).filter(Boolean);
                if (urls.length > 0) return urls;
            }
            for (const v of Object.values(obj)) { const r = find(v, depth+1); if (r) return r; }
            return null;
        }
        return find(state, 0);
    }

    // 1. Новый формат: __staticRouterHydrationData (2026+)
    if (window.__staticRouterHydrationData) {
        const r = extractFrom(window.__staticRouterHydrationData);
        if (r) return r;
    }

    // 2. Старый формат: __preloadedState__
    let ps = window.__preloadedState__;
    if (ps !== undefined && ps !== null) {
        if (typeof ps === 'string') {
            try { ps = JSON.parse(decodeURIComponent(ps)); } catch(e) {
                try { ps = JSON.parse(ps); } catch(e2) { ps = null; }
            }
        }
        if (ps) { const r = extractFrom(ps); if (r) return r; }
    }

    return null;
})()
"""


async def _extract_images_via_js(page: Page) -> list[str]:
    """Извлекает URL изображений через page.evaluate() (CSR fallback)."""
    try:
        result = await page.evaluate(_JS_EXTRACT_IMAGE_URLS)
    except Exception as exc:
        logger.debug(f"page.evaluate() failed: {exc}")
        return []

    if not result or not isinstance(result, list):
        return []

    seen: set[str] = set()
    urls: list[str] = []
    for url in result:
        if isinstance(url, str) and url not in seen:
            urls.append(url)
            seen.add(url)

    if urls:
        logger.debug(f"JS extraction: {len(urls)} HQ image URLs via page.evaluate()")

    return urls


def _safe_get(data: dict, path: list[str]) -> Any:
    """Безопасный обход вложенного dict по списку ключей."""
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _recursive_find_key(data: Any, target_key: str, depth: int = 0) -> Optional[list]:
    """Рекурсивный поиск ключа imageUrls в JSON (fallback)."""
    if depth > _MAX_RECURSION_DEPTH:
        return None

    if isinstance(data, dict):
        if target_key in data:
            value = data[target_key]
            if isinstance(value, list):
                return value
        for value in data.values():
            result = _recursive_find_key(value, target_key, depth + 1)
            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = _recursive_find_key(item, target_key, depth + 1)
            if result is not None:
                return result

    return None


def _parse_json_states(html_text: str) -> list[dict]:
    """Извлекает ВСЕ JSON-состояния из HTML.

    Возвращает список распарсенных dict (от приоритетного к запасному):
    1. __staticRouterHydrationData = JSON.parse("...") — новый (2026+)
    2. __preloadedState__ = "..." — старый (URL-encoded)
    3. __preloadedState__ = {...} — старый (raw JSON)

    Caller сам решает, какое состояние содержит нужные данные.
    """

    patterns = [
        # Новый формат: __staticRouterHydrationData = JSON.parse("escaped JSON")
        (
            r'__staticRouterHydrationData\s*=\s*JSON\.parse\(\s*"((?:[^"\\]|\\.)*)"\s*\)',
            "hydration_escaped",
        ),
        # Старый формат: URL-encoded string
        (
            r'__preloadedState__\s*=\s*"((?:[^"\\]|\\.)*)"',
            "preloaded_urlencoded",
        ),
        # Старый формат: Raw JSON
        (
            r'__preloadedState__\s*=\s*(\{[^<]*)',
            "preloaded_raw",
        ),
    ]

    results: list[dict] = []

    for pattern, format_type in patterns:
        match = re.search(pattern, html_text, re.DOTALL)
        if not match:
            continue

        raw = match.group(1)

        try:
            if format_type == "hydration_escaped":
                # raw содержит escaped JSON: {\"key\":\"val\"}
                # Сначала unescape (как JS string literal), потом parse
                unescaped = json.loads('"' + raw + '"')
                parsed = json.loads(unescaped)
                logger.debug(f"Parsed {format_type}: {len(raw)} chars")
                results.append(parsed)

            elif format_type == "preloaded_urlencoded":
                if raw.startswith("%7B") or raw.startswith("%7b"):
                    json_str = unquote(raw)
                else:
                    continue
                parsed = json.loads(json_str)
                logger.debug(f"Parsed {format_type}: {len(raw)} chars")
                results.append(parsed)

            elif format_type == "preloaded_raw":
                if not raw.startswith("{"):
                    continue
                json_str = _extract_balanced_json(raw)
                if not json_str:
                    continue
                parsed = json.loads(json_str)
                logger.debug(f"Parsed {format_type}: {len(raw)} chars")
                results.append(parsed)

        except json.JSONDecodeError as e:
            logger.debug(f"JSON parse failed ({format_type}): {e}")
            continue

    if not results:
        logger.debug("No JSON state found in HTML")

    return results


def _extract_balanced_json(raw: str) -> Optional[str]:
    """Извлекает сбалансированный JSON из строки, начинающейся с '{'."""
    if not raw.startswith("{"):
        return None

    depth = 0
    in_string = False
    escape = False
    end = 0

    for i, char in enumerate(raw):
        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if char == '"' and not escape:
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == 0:
        return None

    return raw[:end]


def _extract_urls_from_image_data(image_urls: list) -> list[str]:
    """Извлекает URL лучшего качества из массива imageUrls."""
    result: list[str] = []
    seen: set[str] = set()

    for img_data in image_urls:
        if not isinstance(img_data, dict):
            continue

        url: Optional[str] = None
        for size in _SIZE_PRIORITY:
            url = img_data.get(size)
            if url:
                break

        if url and isinstance(url, str) and url not in seen:
            result.append(url)
            seen.add(url)

    return result


async def _extract_images(soup: BeautifulSoup, html: str, page: Page) -> list[str]:
    """
    Извлекает URL изображений высокого качества из HTML карточки Avito.

    Стратегия (по приоритету):
    1. Regex из HTML → __staticRouterHydrationData или __preloadedState__ → imageUrls["1280x960"]
    2. page.evaluate() → window.__staticRouterHydrationData / __preloadedState__ (CSR fallback)
    3. Пустой список (лучше 0 фото, чем 20 мусорных 75x55)

    Args:
        soup: BeautifulSoup объект
        html: Исходный HTML (для regex-парсинга JSON)
        page: Playwright Page (для page.evaluate fallback)

    Returns:
        list[str]: Список URL (пустой если изображений нет)
    """

    # === Стратегия 1: Regex из HTML source ===
    # Перебираем все найденные JSON-состояния (hydration → preloaded)
    for state in _parse_json_states(html):
        # Пробуем известные пути
        image_urls: Optional[list] = None
        for path in _IMAGE_PATHS:
            image_urls = _safe_get(state, path)
            if image_urls and isinstance(image_urls, list):
                logger.debug(f"Found imageUrls via path: {'/'.join(path)}")
                break

        # Fallback: рекурсивный поиск
        if not image_urls:
            image_urls = _recursive_find_key(state, "imageUrls")
            if image_urls:
                logger.debug("Found imageUrls via recursive search")

        if image_urls:
            urls = _extract_urls_from_image_data(image_urls)
            if urls:
                logger.debug(f"Regex extraction: {len(urls)} HQ image URLs")
                return urls

    # === Стратегия 2: page.evaluate() (CSR fallback) ===
    urls = await _extract_images_via_js(page)
    if urls:
        return urls

    # === Стратегия 3: пустой список ===
    logger.debug("No HQ images found — returning empty list (no garbage fallback)")
    return []
