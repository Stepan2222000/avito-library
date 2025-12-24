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

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Page, Response

from avito_library.detectors import (
    detect_page_state,
    CAPTCHA_DETECTOR_ID,
    CARD_FOUND_DETECTOR_ID,
    PROXY_AUTH_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
)
from avito_library.capcha import resolve_captcha_flow

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
    raw_html: Optional[str] = None


class CardParseStatus(Enum):
    """Статус результата парсинга карточки."""
    SUCCESS = "success"
    CAPTCHA_FAILED = "captcha_failed"
    PROXY_BLOCKED = "proxy_blocked"
    NOT_FOUND = "not_found"
    PAGE_NOT_DETECTED = "page_not_detected"


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

# ============================================================================
# Image downloading constants
# ============================================================================

_MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB per file
_RETRY_DELAYS = (1.0, 2.0, 4.0)  # Exponential backoff
_CHUNK_SIZE = 8192
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _validate_image(data: bytes) -> bool:
    """Проверка magic bytes изображения."""
    if len(data) < 12:
        return False
    # JPEG: FF D8 FF
    if data[:3] == b"\xff\xd8\xff":
        return True
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    # WebP: RIFF....WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    # GIF: GIF87a or GIF89a
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    return False


async def _download_images(
    urls: list[str],
    max_concurrent: int = 10,
    timeout: float = 15.0,
) -> tuple[list[bytes], list[str]]:
    """
    Параллельно скачивает изображения.

    Args:
        urls: Список URL изображений
        max_concurrent: Максимум параллельных запросов
        timeout: Таймаут на запрос

    Returns:
        (images: list[bytes], errors: list[str])
    """
    if not urls:
        return [], []

    images: list[bytes] = []
    errors: list[str] = []

    semaphore = asyncio.Semaphore(max_concurrent)

    async def fetch_one(
        client: httpx.AsyncClient, url: str
    ) -> tuple[Optional[bytes], Optional[str]]:
        """Скачивает одно изображение с retry."""
        last_error = ""

        for attempt, delay in enumerate(_RETRY_DELAYS, 1):
            try:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code}"
                        if response.status_code not in _RETRYABLE_STATUS_CODES:
                            break  # Не retryable
                        if attempt < len(_RETRY_DELAYS):
                            await asyncio.sleep(delay)
                        continue

                    # Streaming с лимитом размера
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        total += len(chunk)
                        if total > _MAX_IMAGE_SIZE:
                            return None, f"Size exceeded: {total}"
                        chunks.append(chunk)

                    data = b"".join(chunks)

                    # Валидация magic bytes
                    if not _validate_image(data):
                        return None, "Invalid image format"

                    return data, None

            except httpx.TimeoutException:
                last_error = "Timeout"
            except Exception as e:
                last_error = str(e)

            if attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(delay)

        return None, last_error or "Unknown error"

    async def fetch_with_semaphore(
        client: httpx.AsyncClient, url: str, index: int
    ) -> tuple[int, str, tuple[Optional[bytes], Optional[str]]]:
        async with semaphore:
            return index, url, await fetch_one(client, url)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tasks = [fetch_with_semaphore(client, url, i) for i, url in enumerate(urls)]
        results = await asyncio.gather(*tasks)

    # Сортируем по индексу, собираем результаты
    for index, url, (data, error) in sorted(results):
        if data:
            images.append(data)
        if error:
            errors.append(f"{url}: {error}")

    return images, errors


async def _parse_card_html(
    html: str,
    *,
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
        urls = _extract_images(soup, html)
        data.images_urls = urls
        if urls:
            data.images, data.images_errors = await _download_images(urls)
        else:
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

    # 2. Цикл решения капчи если нужно
    captcha_attempts = 0
    while state in _CAPTCHA_STATES and captcha_attempts < max_captcha_attempts:
        captcha_attempts += 1
        _, solved = await resolve_captcha_flow(page, max_attempts=1)
        if solved:
            state = await detect_page_state(page)

            # Ранний выход при критических ошибках (проблема 1)
            if state in (PROXY_BLOCK_403_DETECTOR_ID, PROXY_AUTH_DETECTOR_ID):
                return CardParseResult(status=CardParseStatus.PROXY_BLOCKED)

            if state == REMOVED_DETECTOR_ID:
                return CardParseResult(status=CardParseStatus.NOT_FOUND)

            # Выход при любом не-капча состоянии (проблема 3)
            if state not in _CAPTCHA_STATES:
                break

    # 3. Обработка финального состояния
    if state == CARD_FOUND_DETECTOR_ID:
        html = await page.content()
        data = await _parse_card_html(html, fields=fields, include_html=include_html)
        return CardParseResult(status=CardParseStatus.SUCCESS, data=data)

    if state in (PROXY_BLOCK_403_DETECTOR_ID, PROXY_AUTH_DETECTOR_ID):
        return CardParseResult(status=CardParseStatus.PROXY_BLOCKED)

    if state == REMOVED_DETECTOR_ID:
        return CardParseResult(status=CardParseStatus.NOT_FOUND)

    if state in _CAPTCHA_STATES:
        return CardParseResult(status=CardParseStatus.CAPTCHA_FAILED)

    # NOT_DETECTED или неизвестный детектор
    return CardParseResult(status=CardParseStatus.PAGE_NOT_DETECTED)


def _is_card_html(soup: BeautifulSoup) -> bool:
    """Uses card_found_detector logic to ensure card markup is present."""

    locator = soup.select_one('span[data-marker="item-view/item-id"]')
    return locator is not None


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
    ["@avito/bx-item-view", "buyerItem", "item", "imageUrls"],
    ["@avito/bx-item-view-v2", "buyerItem", "item", "imageUrls"],
    ["buyerItem", "item", "imageUrls"],
    ["item", "imageUrls"],
]

# Приоритет размеров (от лучшего к худшему)
_SIZE_PRIORITY: list[str] = ["1280x960", "640x480", "150x110", "75x55"]

# Максимальная глубина рекурсивного поиска
_MAX_RECURSION_DEPTH: int = 6


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

    # Обходим массивы тоже (исправление из review)
    elif isinstance(data, list):
        for item in data:
            result = _recursive_find_key(item, target_key, depth + 1)
            if result is not None:
                return result

    return None


def _parse_preloaded_state(html_text: str) -> Optional[dict]:
    """Извлекает и парсит __preloadedState__ из HTML."""

    # Паттерны для разных форматов
    # 1. URL-encoded string (основной формат): __preloadedState__ = "..."
    # 2. Raw JSON (запасной): __preloadedState__ = {...}
    patterns = [
        # URL-encoded - поддержка escaped quotes (исправление из review)
        r'__preloadedState__\s*=\s*"((?:[^"\\]|\\.)*)"',
        # Raw JSON (упрощенный)
        r'__preloadedState__\s*=\s*(\{[^<]*)',
    ]

    for i, pattern in enumerate(patterns):
        match = re.search(pattern, html_text, re.DOTALL)
        if not match:
            continue

        raw = match.group(1)

        try:
            # URL-encoded JSON (начинается с %7B)
            if raw.startswith("%7B") or raw.startswith("%7b"):
                json_str = unquote(raw)
            elif raw.startswith("{"):
                # Raw JSON - пытаемся сбалансировать скобки (исправление из review)
                json_str = _extract_balanced_json(raw)
                if not json_str:
                    continue
            else:
                continue

            return json.loads(json_str)

        except json.JSONDecodeError as e:
            logger.debug(f"JSON parse failed (pattern {i}): {e}")
            continue

    return None


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
    seen: set[str] = set()  # Удаление дубликатов (исправление из review)

    for img_data in image_urls:
        if not isinstance(img_data, dict):
            continue

        # Берём лучшее доступное качество
        url: Optional[str] = None
        for size in _SIZE_PRIORITY:
            url = img_data.get(size)
            if url:
                break

        if url and isinstance(url, str) and url not in seen:
            result.append(url)
            seen.add(url)

    return result


def _extract_images_from_html_gallery(soup: BeautifulSoup) -> list[str]:
    """Fallback: извлечение из HTML галереи (миниатюры)."""
    images: list[str] = []
    seen: set[str] = set()

    # Основной селектор
    for li in soup.select('li[data-marker="image-preview/item"]'):
        img = li.select_one("img")
        if img:
            src = img.get("src") or img.get("data-src")
            if src and isinstance(src, str) and src not in seen:
                images.append(src)
                seen.add(src)

    # Альтернативный селектор
    if not images:
        for img in soup.select('div[data-marker="item-view/gallery"] img'):
            src = img.get("src")
            if src and isinstance(src, str) and src not in seen:
                images.append(src)
                seen.add(src)

    return images


def _extract_images_from_og_meta(soup: BeautifulSoup) -> list[str]:
    """Fallback: извлечение из OpenGraph meta-тегов."""
    images: list[str] = []
    for meta in soup.select('meta[property="og:image"]'):
        content = meta.get("content")
        if content and isinstance(content, str):
            images.append(content)
    return images


def _extract_images(soup: BeautifulSoup, html: str) -> list[str]:
    """
    Извлекает URL изображений высокого качества из HTML карточки Avito.

    Стратегия (по приоритету):
    1. JSON из __preloadedState__ → imageUrls[]["1280x960"]
    2. HTML галерея → li[data-marker="image-preview/item"] img
    3. OpenGraph meta → meta[property="og:image"]

    Args:
        soup: BeautifulSoup объект
        html: Исходный HTML (для парсинга JSON без str(soup))

    Returns:
        list[str]: Список URL (пустой если изображений нет)
    """

    # === Стратегия 1: __preloadedState__ JSON ===
    state = _parse_preloaded_state(html)

    if state:
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
                logger.debug(f"Extracted {len(urls)} HQ image URLs from JSON")
                return urls

    # === Стратегия 2: HTML галерея ===
    urls = _extract_images_from_html_gallery(soup)
    if urls:
        logger.debug(f"Using HTML gallery fallback: {len(urls)} URLs")
        return urls

    # === Стратегия 3: OpenGraph meta ===
    urls = _extract_images_from_og_meta(soup)
    if urls:
        logger.debug(f"Using og:image fallback: {len(urls)} URLs")
        return urls

    logger.debug("No images found in HTML")
    return []
