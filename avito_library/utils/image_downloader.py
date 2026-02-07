"""Утилиты для скачивания изображений."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

# Отключаем излишнее логирование HTTP-запросов от httpx
logging.getLogger("httpx").setLevel(logging.WARNING)

__all__ = [
    "ImageResult",
    "MAX_IMAGE_SIZE",
    "RETRY_DELAYS",
    "CHUNK_SIZE",
    "RETRYABLE_STATUS_CODES",
    "validate_image",
    "detect_format",
    "download_images",
]

# Константы
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB per file
RETRY_DELAYS = (1.0, 2.0, 4.0)  # Exponential backoff
CHUNK_SIZE = 8192
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


@dataclass(slots=True)
class ImageResult:
    """Результат скачивания одного изображения."""

    url: str
    success: bool
    data: bytes | None = None
    size: int = 0
    format: str | None = None
    error: str | None = None


def detect_format(data: bytes) -> str | None:
    """Определяет формат изображения по magic bytes.

    Returns:
        "jpeg", "png", "webp", "gif" или None если формат неизвестен.
    """
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def validate_image(data: bytes) -> bool:
    """Проверка magic bytes изображения (JPEG, PNG, WebP, GIF)."""
    return detect_format(data) is not None


async def download_images(
    urls: list[str],
    max_concurrent: int = 10,
    timeout: float = 15.0,
) -> list[ImageResult]:
    """
    Параллельно скачивает изображения.

    Args:
        urls: Список URL изображений
        max_concurrent: Максимум параллельных запросов
        timeout: Таймаут на запрос

    Returns:
        Список ImageResult для каждого URL (порядок соответствует входным URL).
    """
    if not urls:
        return []

    semaphore = asyncio.Semaphore(max_concurrent)

    async def fetch_one(
        client: httpx.AsyncClient, url: str
    ) -> ImageResult:
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
                            return ImageResult(
                                url=url,
                                success=False,
                                size=total,
                                error=f"Size exceeded: {total}",
                            )
                        chunks.append(chunk)

                    data = b"".join(chunks)
                    fmt = detect_format(data)

                    # Валидация magic bytes
                    if fmt is None:
                        return ImageResult(
                            url=url,
                            success=False,
                            size=len(data),
                            error="Invalid image format",
                        )

                    return ImageResult(
                        url=url,
                        success=True,
                        data=data,
                        size=len(data),
                        format=fmt,
                    )

            except httpx.TimeoutException:
                last_error = "Timeout"
            except Exception as e:
                last_error = str(e)

            if attempt < len(RETRY_DELAYS):
                await asyncio.sleep(delay)

        return ImageResult(
            url=url,
            success=False,
            error=last_error or "Unknown error",
        )

    async def fetch_with_semaphore(
        client: httpx.AsyncClient, url: str, index: int
    ) -> tuple[int, ImageResult]:
        async with semaphore:
            return index, await fetch_one(client, url)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tasks = [fetch_with_semaphore(client, url, i) for i, url in enumerate(urls)]
        raw_results = await asyncio.gather(*tasks)

    # Сортируем по индексу — порядок результатов соответствует входным URL
    return [result for _, result in sorted(raw_results)]
