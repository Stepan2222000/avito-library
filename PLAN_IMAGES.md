# Оптимальный алгоритм парсинга и скачивания изображений Avito

## Цель
1. Извлечение URL изображений высокого качества (1280x960) из HTML
2. Безопасное скачивание бинарных данных изображений

---

## Часть 1: Извлечение URL изображений

### Источник данных
JSON в `window.__preloadedState__` (URL-encoded)

### Путь к данным
```
@avito/bx-item-view → buyerItem → item → imageUrls[]
```

### Структура imageUrls
```json
[
  {"75x55": "url1", "150x110": "url2", "640x480": "url3", "1280x960": "url4"},
  ...
]
```

### Полный код _extract_images()

```python
import json
import logging
import re
from typing import Optional
from urllib.parse import unquote
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Альтернативные пути к imageUrls (защита от изменений структуры Avito)
_IMAGE_PATHS = [
    ['@avito/bx-item-view', 'buyerItem', 'item', 'imageUrls'],
    ['@avito/bx-item-view-v2', 'buyerItem', 'item', 'imageUrls'],
    ['buyerItem', 'item', 'imageUrls'],
    ['item', 'imageUrls'],
]

# Приоритет размеров (от лучшего к худшему)
_SIZE_PRIORITY = ['1280x960', '640x480', '150x110', '75x55']


def _safe_get(data: dict, path: list[str]):
    """Безопасный обход вложенного dict по списку ключей."""
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _recursive_find_key(data: dict, target_key: str, max_depth: int = 6):
    """Рекурсивный поиск ключа в JSON (fallback)."""
    if max_depth <= 0 or not isinstance(data, dict):
        return None

    if target_key in data:
        value = data[target_key]
        if isinstance(value, list):
            return value

    for value in data.values():
        if isinstance(value, dict):
            result = _recursive_find_key(value, target_key, max_depth - 1)
            if result is not None:
                return result
    return None


def _parse_preloaded_state(html_text: str) -> Optional[dict]:
    """Извлекает и парсит __preloadedState__ из HTML."""

    # Паттерны для разных форматов
    patterns = [
        # URL-encoded string (основной формат)
        r'__preloadedState__\s*=\s*"([^"]+)"',
        # Raw JSON (запасной)
        r'__preloadedState__\s*=\s*(\{.+?\})\s*;?\s*(?:</script>|window\.)',
    ]

    for pattern in patterns:
        match = re.search(pattern, html_text, re.DOTALL)
        if not match:
            continue

        raw = match.group(1)

        try:
            # Определяем формат
            if raw.startswith('%7B') or raw.startswith('%7b'):
                # URL-encoded
                json_str = unquote(raw)
            elif raw.startswith('{'):
                # Raw JSON
                json_str = raw
            else:
                continue

            return json.loads(json_str)

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse __preloadedState__ JSON: {e}")
            continue

    return None


def _extract_urls_from_image_data(image_urls: list) -> list[str]:
    """Извлекает URL лучшего качества из массива imageUrls."""
    result = []

    for img_data in image_urls:
        if not isinstance(img_data, dict):
            continue

        # Берём лучшее доступное качество
        url = None
        for size in _SIZE_PRIORITY:
            url = img_data.get(size)
            if url:
                break

        if url and isinstance(url, str):
            result.append(url)

    return result


def _extract_images_from_html_gallery(soup: BeautifulSoup) -> list[str]:
    """Fallback: извлечение из HTML галереи (миниатюры)."""
    images = []

    # Основной селектор
    for li in soup.select('li[data-marker="image-preview/item"]'):
        img = li.select_one('img')
        if img:
            src = img.get('src') or img.get('data-src')
            if src:
                images.append(src)

    # Альтернативный селектор
    if not images:
        for img in soup.select('div[data-marker="item-view/gallery"] img'):
            src = img.get('src')
            if src and 'img.avito' in src:
                images.append(src)

    return images


def _extract_images_from_og_meta(soup: BeautifulSoup) -> list[str]:
    """Fallback: извлечение из OpenGraph meta-тегов."""
    images = []
    for meta in soup.select('meta[property="og:image"]'):
        content = meta.get('content')
        if content:
            images.append(content)
    return images


def _extract_images(soup: BeautifulSoup) -> list[str]:
    """
    Извлекает URL изображений высокого качества из HTML карточки Avito.

    Стратегия (по приоритету):
    1. JSON из __preloadedState__ → imageUrls[]["1280x960"]
    2. HTML галерея → li[data-marker="image-preview/item"] img
    3. OpenGraph meta → meta[property="og:image"]

    Returns:
        list[str]: Список URL (пустой если изображений нет)
    """

    # === Стратегия 1: __preloadedState__ JSON ===
    html_text = str(soup)
    state = _parse_preloaded_state(html_text)

    if state:
        # Пробуем известные пути
        image_urls = None
        for path in _IMAGE_PATHS:
            image_urls = _safe_get(state, path)
            if image_urls and isinstance(image_urls, list):
                logger.debug(f"Found imageUrls via path: {'/'.join(path)}")
                break

        # Fallback: рекурсивный поиск
        if not image_urls:
            image_urls = _recursive_find_key(state, 'imageUrls')
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
```

---

## Часть 2: Скачивание изображений

### Требования безопасности
1. **SSRF защита** — только хосты `*.img.avito.st`
2. **DoS защита** — лимит 10MB на файл
3. **Валидация** — проверка magic bytes
4. **Retry** — 3 попытки с exponential backoff
5. **Path traversal** — санитизация prefix

### Полный код ImageDownloader

```python
# avito_library/utils/image_downloader.py

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Literal
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# === Константы безопасности ===
ALLOWED_HOST_SUFFIX = '.img.avito.st'
ALLOWED_HOSTS = {'img.avito.st'}  # Для точного совпадения
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_RETRIES = 3
RETRY_DELAYS = (1.0, 2.0, 4.0)  # Exponential backoff
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


# === Валидация ===

def validate_url(url: str) -> tuple[bool, str]:
    """
    Проверяет что URL принадлежит CDN Avito.

    Returns:
        (is_valid, error_message)
    """
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ('http', 'https'):
            return False, f"Invalid scheme: {parsed.scheme}"

        host = parsed.hostname
        if not host:
            return False, "No hostname in URL"

        # Проверка хоста
        if host in ALLOWED_HOSTS:
            return True, ""
        if host.endswith(ALLOWED_HOST_SUFFIX):
            return True, ""

        return False, f"Host not allowed: {host}"

    except Exception as e:
        return False, f"URL parse error: {e}"


def validate_image_data(data: bytes) -> tuple[bool, str]:
    """
    Проверяет magic bytes изображения.

    Returns:
        (is_valid, detected_format)
    """
    if len(data) < 12:
        return False, "too_small"

    # JPEG: FF D8 FF
    if data[:3] == b'\xff\xd8\xff':
        return True, "jpeg"

    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return True, "png"

    # WebP: RIFF....WEBP
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return True, "webp"

    # GIF: GIF87a или GIF89a
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return True, "gif"

    return False, "unknown"


def get_extension(format_name: str) -> str:
    """Возвращает расширение файла по формату."""
    return {
        'jpeg': '.jpg',
        'png': '.png',
        'webp': '.webp',
        'gif': '.gif',
    }.get(format_name, '.bin')


def sanitize_filename(prefix: str) -> str:
    """Очищает prefix от опасных символов."""
    # Оставляем только буквы, цифры, дефис, подчёркивание
    safe = ''.join(c for c in prefix if c.isalnum() or c in '-_')
    return safe[:100] if safe else 'img'  # Лимит длины


# === Результат загрузки ===

@dataclass
class DownloadResult:
    """Результат скачивания одного изображения."""
    url: str
    data: Optional[bytes] = None
    path: Optional[Path] = None
    error: Optional[str] = None
    format: Optional[str] = None
    attempts: int = 0

    @property
    def success(self) -> bool:
        return self.error is None and self.data is not None

    @property
    def size(self) -> int:
        return len(self.data) if self.data else 0


@dataclass
class BatchDownloadResult:
    """Результат пакетной загрузки."""
    results: list[DownloadResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed_count(self) -> int:
        return self.total - self.success_count

    @property
    def total_bytes(self) -> int:
        return sum(r.size for r in self.results)

    @property
    def successful(self) -> list[DownloadResult]:
        return [r for r in self.results if r.success]

    @property
    def failed(self) -> list[DownloadResult]:
        return [r for r in self.results if not r.success]


# === Загрузчик ===

class ImageDownloader:
    """
    Асинхронный загрузчик изображений Avito с защитой от SSRF/DoS.

    Использование:
        async with ImageDownloader() as downloader:
            result = await downloader.download_one(url)
            results = await downloader.download_many(urls)
            results = await downloader.download_to_dir(urls, Path("./images"))
    """

    def __init__(
        self,
        timeout: float = 15.0,
        max_concurrent: int = 10,
        max_file_size: int = MAX_IMAGE_SIZE,
        validate_images: bool = True,
    ):
        """
        Args:
            timeout: Таймаут на один запрос (секунды)
            max_concurrent: Максимум параллельных загрузок
            max_file_size: Максимальный размер файла (байты)
            validate_images: Проверять ли magic bytes
        """
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.max_file_size = max_file_size
        self.validate_images = validate_images
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> 'ImageDownloader':
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=True,
            http2=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def download_one(self, url: str) -> DownloadResult:
        """
        Скачивает одно изображение с retry и валидацией.

        Args:
            url: URL изображения

        Returns:
            DownloadResult с данными или ошибкой
        """
        result = DownloadResult(url=url)

        # === SSRF защита ===
        is_valid, error = validate_url(url)
        if not is_valid:
            logger.warning(f"URL validation failed: {url} - {error}")
            result.error = f"URL not allowed: {error}"
            return result

        # === Retry loop ===
        last_error = ""
        for attempt, delay in enumerate(RETRY_DELAYS, 1):
            result.attempts = attempt

            try:
                data = await self._fetch_with_size_limit(url)

                # === Валидация изображения ===
                if self.validate_images:
                    is_image, fmt = validate_image_data(data)
                    if not is_image:
                        result.error = f"Invalid image data (detected: {fmt})"
                        return result
                    result.format = fmt
                else:
                    result.format = "unknown"

                result.data = data
                logger.debug(f"Downloaded {url}: {len(data)} bytes ({result.format})")
                return result

            except httpx.TimeoutException:
                last_error = "Request timeout"
                logger.debug(f"Attempt {attempt}/{MAX_RETRIES} timeout: {url}")

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in RETRYABLE_STATUS_CODES:
                    last_error = f"HTTP {status}"
                    logger.debug(f"Attempt {attempt}/{MAX_RETRIES} HTTP {status}: {url}")
                else:
                    result.error = f"HTTP {status}: {e.response.reason_phrase}"
                    return result

            except SizeExceededError as e:
                result.error = str(e)
                return result

            except Exception as e:
                last_error = str(e)
                logger.debug(f"Attempt {attempt}/{MAX_RETRIES} error: {url} - {e}")

            # Ждём перед retry (кроме последней попытки)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(delay)

        result.error = f"Failed after {MAX_RETRIES} attempts: {last_error}"
        return result

    async def _fetch_with_size_limit(self, url: str) -> bytes:
        """Скачивает с контролем размера (защита от DoS)."""
        async with self._client.stream("GET", url) as response:
            response.raise_for_status()

            # Проверка Content-Length
            content_length = response.headers.get('content-length')
            if content_length:
                size = int(content_length)
                if size > self.max_file_size:
                    raise SizeExceededError(
                        f"Content-Length {size} exceeds limit {self.max_file_size}"
                    )

            # Читаем с контролем размера
            chunks = []
            total = 0
            async for chunk in response.aiter_bytes(chunk_size=8192):
                total += len(chunk)
                if total > self.max_file_size:
                    raise SizeExceededError(
                        f"Download size {total} exceeds limit {self.max_file_size}"
                    )
                chunks.append(chunk)

            return b''.join(chunks)

    async def download_many(
        self,
        urls: list[str],
        on_progress: Optional[callable] = None,
    ) -> BatchDownloadResult:
        """
        Скачивает несколько изображений параллельно.

        Args:
            urls: Список URL
            on_progress: Callback(completed: int, total: int) для прогресса

        Returns:
            BatchDownloadResult со всеми результатами
        """
        if not urls:
            return BatchDownloadResult()

        semaphore = asyncio.Semaphore(self.max_concurrent)
        completed = 0
        total = len(urls)

        async def fetch_with_semaphore(url: str) -> DownloadResult:
            nonlocal completed
            async with semaphore:
                result = await self.download_one(url)
                completed += 1
                if on_progress:
                    on_progress(completed, total)
                return result

        tasks = [fetch_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks)

        return BatchDownloadResult(results=list(results))

    async def download_to_dir(
        self,
        urls: list[str],
        output_dir: Path,
        prefix: str = "img",
        on_progress: Optional[callable] = None,
    ) -> BatchDownloadResult:
        """
        Скачивает изображения и сохраняет в директорию.

        Args:
            urls: Список URL
            output_dir: Директория для сохранения
            prefix: Префикс имён файлов (будет санитизирован)
            on_progress: Callback для прогресса

        Returns:
            BatchDownloadResult с путями к файлам
        """
        # Санитизация и подготовка директории
        safe_prefix = sanitize_filename(prefix)
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Скачиваем
        batch_result = await self.download_many(urls, on_progress)

        # Сохраняем успешные
        for i, result in enumerate(batch_result.results):
            if not result.success:
                continue

            ext = get_extension(result.format or 'jpeg')
            filename = f"{safe_prefix}_{i:04d}{ext}"
            filepath = (output_dir / filename).resolve()

            # Path traversal защита
            if not str(filepath).startswith(str(output_dir)):
                result.error = "Path traversal detected"
                result.data = None
                continue

            filepath.write_bytes(result.data)
            result.path = filepath
            logger.debug(f"Saved: {filepath}")

        return batch_result


class SizeExceededError(Exception):
    """Размер файла превышает лимит."""
    pass
```

---

## Часть 3: Интеграция

### Изменения в card_parser.py

```python
# 1. Добавить импорты
import json
import logging
from urllib.parse import unquote

logger = logging.getLogger(__name__)

# 2. Добавить "images" в _SUPPORTED_FIELDS
_SUPPORTED_FIELDS = {
    "title", "price", "seller", "item_id", "published_at",
    "description", "location", "characteristics", "views_total",
    "raw_html",
    "images",  # ДОБАВИТЬ
}

# 3. Вставить функции из Части 1 (после _extract_views)

# 4. В parse_card() добавить после views_total:
if "images" in requested_fields:
    data.images = _extract_images(soup)
```

### Экспорт в __init__.py

```python
from avito_library.utils.image_downloader import (
    ImageDownloader,
    DownloadResult,
    BatchDownloadResult,
)
```

### Зависимости (pyproject.toml)

```toml
dependencies = [
    ...
    "httpx>=0.25.0",
]
```

---

## Часть 4: Примеры использования

### Базовый пример

```python
import asyncio
from pathlib import Path
from avito_library import parse_card, ImageDownloader

async def main():
    # 1. Парсим HTML
    with open("card.html") as f:
        html = f.read()

    card = parse_card(html, fields=['title', 'item_id', 'images'])
    print(f"Товар: {card.title}")
    print(f"Найдено изображений: {len(card.images)}")

    # 2. Скачиваем
    async with ImageDownloader() as dl:
        results = await dl.download_to_dir(
            card.images,
            Path(f"./downloads/{card.item_id}"),
            prefix=str(card.item_id)
        )

        print(f"Успешно: {results.success_count}/{results.total}")
        print(f"Размер: {results.total_bytes / 1024:.1f} KB")

        for r in results.failed:
            print(f"Ошибка: {r.url} - {r.error}")

asyncio.run(main())
```

### С прогрессом

```python
async def download_with_progress(urls, output_dir):
    def on_progress(done, total):
        print(f"\rПрогресс: {done}/{total}", end="", flush=True)

    async with ImageDownloader(max_concurrent=5) as dl:
        results = await dl.download_to_dir(
            urls, output_dir,
            prefix="item",
            on_progress=on_progress
        )

    print()  # Новая строка после прогресса
    return results
```

### Только в память (без сохранения)

```python
async with ImageDownloader() as dl:
    results = await dl.download_many(urls)

    for r in results.successful:
        # r.data содержит bytes изображения
        process_image(r.data)
```

---

## Часть 5: Тестирование

### Тест парсинга URL

```python
# test_extract_images.py
from bs4 import BeautifulSoup

def test_extract_from_preloaded_state():
    with open("trash/index.html") as f:
        html = f.read()
    soup = BeautifulSoup(html, "lxml")

    images = _extract_images(soup)

    assert len(images) == 20
    assert all(url.startswith("https://") for url in images)
    assert all("img.avito" in url for url in images)
    assert "1280x960" in images[0] or "640x480" in images[0]

def test_extract_empty_gallery():
    html = "<html><body>No images</body></html>"
    soup = BeautifulSoup(html, "lxml")

    images = _extract_images(soup)

    assert images == []

def test_fallback_to_html_gallery():
    html = '''
    <html><body>
    <li data-marker="image-preview/item">
        <img src="https://00.img.avito.st/test.jpg">
    </li>
    </body></html>
    '''
    soup = BeautifulSoup(html, "lxml")

    images = _extract_images(soup)

    assert len(images) == 1
    assert images[0] == "https://00.img.avito.st/test.jpg"
```

### Тест безопасности

```python
# test_security.py
import pytest

def test_ssrf_protection():
    # Должен отклонить
    assert validate_url("file:///etc/passwd")[0] == False
    assert validate_url("http://localhost:8080")[0] == False
    assert validate_url("http://169.254.169.254")[0] == False
    assert validate_url("http://evil.com/image.jpg")[0] == False

    # Должен принять
    assert validate_url("https://00.img.avito.st/image.jpg")[0] == True
    assert validate_url("https://img.avito.st/image.jpg")[0] == True

def test_path_traversal():
    assert sanitize_filename("../../../etc") == "etc"
    assert sanitize_filename("normal_name") == "normal_name"
    assert sanitize_filename("") == "img"
```

### Интеграционный тест (требует сеть)

```bash
python3 << 'EOF'
import asyncio
from pathlib import Path
from bs4 import BeautifulSoup

# Вставить код _extract_images и ImageDownloader сюда
# ...

async def integration_test():
    with open("trash/index.html") as f:
        html = f.read()

    soup = BeautifulSoup(html, "lxml")
    urls = _extract_images(soup)

    print(f"Найдено URL: {len(urls)}")

    async with ImageDownloader(max_concurrent=5) as dl:
        # Тест 1: скачать один
        result = await dl.download_one(urls[0])
        assert result.success, f"Failed: {result.error}"
        print(f"Тест 1 OK: {result.size} bytes, format={result.format}")

        # Тест 2: скачать первые 3
        results = await dl.download_many(urls[:3])
        assert results.success_count == 3
        print(f"Тест 2 OK: {results.success_count}/3")

        # Тест 3: сохранить в директорию
        results = await dl.download_to_dir(urls[:5], Path("./test_images"), prefix="test")
        print(f"Тест 3 OK: {results.success_count}/5 saved")

        for r in results.successful:
            print(f"  {r.path}")

asyncio.run(integration_test())
EOF
```
