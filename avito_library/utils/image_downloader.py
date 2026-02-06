"""Утилиты для скачивания изображений."""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx

__all__ = [
    "MAX_IMAGE_SIZE",
    "RETRY_DELAYS",
    "CHUNK_SIZE",
    "RETRYABLE_STATUS_CODES",
    "validate_image",
    "download_images",
]

# Константы
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB per file
RETRY_DELAYS = (1.0, 2.0, 4.0)  # Exponential backoff
CHUNK_SIZE = 8192
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def validate_image(data: bytes) -> bool:
    """Проверка magic bytes изображения (JPEG, PNG, WebP, GIF)."""
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


async def download_images(
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

        for attempt, delay in enumerate(RETRY_DELAYS, 1):
            try:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code}"
                        if response.status_code not in RETRYABLE_STATUS_CODES:
                            break  # Не retryable
                        if attempt < len(RETRY_DELAYS):
                            await asyncio.sleep(delay)
                        continue

                    # Streaming с лимитом размера
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes(CHUNK_SIZE):
                        total += len(chunk)
                        if total > MAX_IMAGE_SIZE:
                            return None, f"Size exceeded: {total}"
                        chunks.append(chunk)

                    data = b"".join(chunks)

                    # Валидация magic bytes
                    if not validate_image(data):
                        return None, "Invalid image format"

                    return data, None

            except httpx.TimeoutException:
                last_error = "Timeout"
            except Exception as e:
                last_error = str(e)

            if attempt < len(RETRY_DELAYS):
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
